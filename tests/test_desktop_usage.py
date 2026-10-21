"""真实桌面工作进程使用模拟 API，检查不限额的用量统计和保存。"""

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from desktop_app.service import DesktopService
from tests.streaming_support import model_response
from llm.usage import UsageRun


def usage_worker(connection, payload):
    from anthropic import Anthropic
    import httpx2
    from desktop_app.worker import run_worker

    sequence = 0
    def respond(request):
        nonlocal sequence
        body = json.loads(request.content)
        if "tools" not in body:
            content = [{"type": "text", "text": "[]"}]
        elif payload.get("previous_usage") or payload.get("active_request") == "新任务" or sequence >= 65:
            content = [{"type": "text", "text": "处理完成"}]
        else:
            sequence += 1
            content = [{"type": "tool_use", "id": f"read_{sequence}", "name": "read_file", "input": {"path": "sample.txt"}}]
        return model_response(content, stream=body.get("stream", False))

    with Anthropic(api_key="test-key", base_url="https://example.test", max_retries=0,
                   http_client=httpx2.Client(transport=httpx2.MockTransport(respond))) as model, \
            patch("anthropic.Anthropic", return_value=model), patch("dotenv.load_dotenv"), \
            patch.dict(os.environ, {"MODEL_ID": "test-model", "ANTHROPIC_API_KEY": "test-key"}):
        run_worker(connection, payload)


def waiting_usage_worker(connection, payload):
    run = UsageRun(report=lambda usage: connection.send({"type": "usage", "usage": usage}))
    run.begin("main")
    time.sleep(30)


class DesktopUsageTests(unittest.TestCase):
    def test_interrupted_request_is_unknown_after_stop_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DesktopService(root, root / "sessions", worker_target=waiting_usage_worker)
            sid = app.create_session()["data"]["id"]
            try:
                app.send_message(sid, "等待模型")
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if app.get_session(sid)["data"]["usage"]:
                        break
                    time.sleep(0.03)
                self.assertEqual(app.get_session(sid)["data"]["usage"]["pending_calls"], 1)
                app.stop(sid)
                app.shutdown()
                stopped = app.get_session(sid)["data"]
                self.assertEqual(stopped["usage"]["unknown_calls"], 1)
                self.assertEqual(stopped["usage"]["pending_calls"], 0)
                self.assertEqual(stopped["usage"]["status"], "stopped")
                # 模拟桌面突然关闭后磁盘仅留下请求开始记录。
                run = UsageRun()
                run.begin("main")
                session = app._sessions[sid]
                session["status"] = "running"
                app._record_usage(session, run.snapshot())
                app._save(session)
                restored = DesktopService(root, root / "sessions", worker_target=waiting_usage_worker)
                self.addCleanup(restored.shutdown)
                usage = restored.get_session(sid)["data"]["usage"]
                self.assertEqual(usage["status"], "stopped")
                self.assertEqual(usage["unknown_calls"], 1)
                self.assertEqual(usage["pending_calls"], 0)
                self.assertEqual(usage["by_purpose"]["main"]["unknown_calls"], 1)
            finally:
                app.shutdown()

    def test_more_than_sixty_requests_complete_and_restart_keeps_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.txt").write_text("测试内容", encoding="utf-8")
            app = DesktopService(root, root / "sessions", worker_target=usage_worker)
            sid = app.create_session()["data"]["id"]

            def wait(status):
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    session = app.get_session(sid)["data"]
                    if session["status"] == status:
                        return session
                    time.sleep(0.03)
                self.fail(f"预期 {status}，实际 {session['status']}: {session['events'][-3:]}")

            try:
                app.send_message(sid, "循环任务")
                completed = wait("completed")
                run_id = completed["usage"]["run_id"]
                calls = completed["usage"]["calls"]
                self.assertGreaterEqual(calls, 67)
                self.assertEqual(completed["usage_total"]["calls"], calls)
                self.assertEqual(completed["usage"]["by_purpose"]["memory"]["calls"], 1)
                self.assertEqual(sum(e["type"] == "user" for e in completed["events"]), 1)
                self.assertNotIn("limits", completed["usage"])
                self.assertNotIn("warning", completed["usage"])
                self.assertFalse(any(e["type"] == "notice" and "80%" in e.get("text", "") for e in completed["events"]))
                self.assertEqual(sum(e["type"] == "tool_start" for e in completed["events"]), 65)
                app.send_message(sid, "新任务")
                fresh = wait("completed")
                self.assertNotEqual(fresh["usage"]["run_id"], run_id)
                self.assertEqual(fresh["usage"]["calls"], 2)
                self.assertEqual(fresh["usage_total"]["calls"], calls + 2)
                app.shutdown()
                app = DesktopService(root, root / "sessions", worker_target=usage_worker)
                self.assertEqual(app.get_session(sid)["data"]["usage_total"]["calls"], calls + 2)
                self.assertEqual(app.poll(sid)["data"]["usage"]["calls"], 2)
            finally:
                app.shutdown()

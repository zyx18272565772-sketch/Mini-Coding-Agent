"""真实 SDK、模拟网络：手动续跑失败任务，保留用量及文件进度。"""

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from desktop_app.service import DesktopService
from tests.streaming_support import encode_event, message_events, model_response


def retry_worker(connection, payload):
    from anthropic import Anthropic
    import httpx2
    from desktop_app.worker import run_worker

    query = payload["active_request"]
    resumed = payload.get("previous_usage") is not None
    initial_user_count = sum(message["role"] == "user" and isinstance(message["content"], str) for message in payload["messages"])

    def respond(request):
        body = json.loads(request.content)
        if "tools" not in body:
            return model_response([{"type": "text", "text": "[]"}], stream=body.get("stream", False))
        if "认证" in query:
            return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "测试认证失败"}})
        task_start = max(index for index, message in enumerate(body["messages"])
                         if message["role"] == "user" and message["content"] == query)
        task_messages = body["messages"][task_start:]
        if resumed and "持续失败" not in query:
            if "断流" not in query:
                results = [block for message in task_messages if isinstance(message.get("content"), list)
                           for block in message["content"] if block.get("type") == "tool_result"]
                assert any(block["tool_use_id"] == "saved_write" for block in results), "继续必须保留已完成工具的结果"
            assert sum(message["role"] == "user" and isinstance(message["content"], str) for message in body["messages"]) == initial_user_count, "不能重复添加用户任务"
            time.sleep(0.1)
            return model_response([{"type": "text", "text": "已从保存的进度继续完成。"}], stream=body.get("stream", False))
        if "断流" in query:
            content = [{"type": "text", "text": "已生成的部分说明"},
                       {"type": "tool_use", "id": "incomplete_write", "name": "write_file", "input": {"path": "unsafe.txt", "content": "不能执行"}}]
            return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                                  content=b"".join(encode_event(event) for event in message_events(content, complete=False)))
        completed = any(isinstance(message.get("content"), list) and any(block.get("type") == "tool_result" for block in message["content"])
                        for message in task_messages)
        if not completed and "持续失败" not in query:
            return model_response([{"type": "tool_use", "id": "saved_write", "name": "write_file",
                                    "input": {"path": "progress.txt", "content": "第一次写入"}}], stream=body.get("stream", False))
        if "超时" in query:
            raise httpx2.ReadTimeout("模拟响应超时", request=request)
        if "服务错误" in query:
            return httpx2.Response(503, json={"type": "error", "error": {"type": "overloaded_error", "message": "测试服务暂不可用"}})
        raise httpx2.ConnectError("模拟连接中断", request=request)

    with Anthropic(api_key="test-key", base_url="https://example.test", max_retries=0,
                   http_client=httpx2.Client(transport=httpx2.MockTransport(respond))) as model, \
            patch("anthropic.Anthropic", return_value=model), patch("dotenv.load_dotenv"), \
            patch.dict(os.environ, {"MODEL_ID": "test-model", "ANTHROPIC_API_KEY": "test-key"}):
        run_worker(connection, payload)


class DesktopRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = DesktopService(self.root, self.root / "sessions", worker_target=retry_worker)
        self.sid = self.app.create_session()["data"]["id"]

    def tearDown(self):
        self.app.shutdown()
        self.temp.cleanup()

    def wait(self, status):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            session = self.app.get_session(self.sid)["data"]
            if session["status"] == status:
                return session
            time.sleep(0.03)
        self.fail(f"预期 {status}，实际 {session['status']}：{session['events'][-3:]}")

    def test_connection_timeout_and_server_failure_resume_without_replaying_tools(self):
        for kind, query in (("connection", "失败后继续验收"), ("timeout", "失败后继续：超时"), ("server", "失败后继续：服务错误")):
            with self.subTest(kind=kind):
                self.sid = self.app.create_session()["data"]["id"]
                self.assertFalse(self.app.retry_run(self.sid)["ok"])
                self.app.send_message(self.sid, query)
                failed = self.wait("error")
                self.assertTrue(failed["failure"]["retryable"])
                self.assertEqual(failed["failure"]["kind"], kind)
                self.assertEqual(failed["usage"]["calls"], 2)
                self.assertEqual(failed["usage"]["unknown_calls"], 1)
                self.assertEqual(self.app.poll(self.sid)["data"]["failure"], failed["failure"])
                (self.root / "progress.txt").write_text("失败后保留的进度", encoding="utf-8")
                self.app.shutdown()
                self.app = DesktopService(self.root, self.root / "sessions", worker_target=retry_worker)
                self.assertTrue(self.app.retry_run(self.sid)["ok"])
                self.assertFalse(self.app.retry_run(self.sid)["ok"])
                done = self.wait("completed")
                self.assertEqual(done["usage"]["run_id"], failed["usage"]["run_id"])
                self.assertEqual(done["usage"]["calls"], 4)
                self.assertNotIn("limits", done["usage"])
                self.assertEqual(done["usage"]["unknown_calls"], 1)
                self.assertEqual(done["usage_total"]["calls"], 4)
                self.assertIsNone(done["failure"])
                self.assertEqual(sum(event["type"] == "user" for event in done["events"]), 1)
                self.assertEqual(sum(event["type"] == "tool_start" for event in done["events"]), 1)
                self.assertEqual((self.root / "progress.txt").read_text(encoding="utf-8"), "失败后保留的进度")

    def test_interrupted_stream_keeps_partial_text_and_never_executes_partial_tool(self):
        self.app.send_message(self.sid, "失败后继续：断流")
        failed = self.wait("error")
        self.assertEqual(failed["failure"]["kind"], "stream_interrupted")
        self.assertTrue(any(event["type"] == "assistant_end" and event.get("interrupted") for event in failed["events"]))
        self.assertFalse((self.root / "unsafe.txt").exists())
        self.assertTrue(self.app.retry_run(self.sid)["ok"])
        done = self.wait("completed")
        self.assertEqual(done["usage"]["calls"], 3)
        self.assertEqual(sum(event["type"] == "user" for event in done["events"]), 1)
        self.assertFalse((self.root / "unsafe.txt").exists())

    def test_repeated_failure_requires_manual_retry_and_keeps_cumulative_counts(self):
        self.app.send_message(self.sid, "失败后继续：持续失败")
        first = self.wait("error")
        self.assertEqual(first["usage"]["calls"], 1)
        self.assertTrue(self.app.retry_run(self.sid)["ok"])
        second = self.wait("error")
        self.assertEqual(second["usage"]["run_id"], first["usage"]["run_id"])
        self.assertEqual(second["usage"]["calls"], 2)
        self.assertTrue(second["failure"]["retryable"])

    def test_authentication_failure_and_changed_workspace_cannot_resume(self):
        self.app.send_message(self.sid, "失败后继续：认证")
        failed = self.wait("error")
        self.assertFalse(failed["failure"]["retryable"])
        self.assertFalse(self.app.retry_run(self.sid)["ok"])
        self.app.send_message(self.sid, "失败后继续：持续失败")
        self.wait("error")
        self.app._sessions[self.sid]["workdir"] = str(self.root / "different")
        self.assertFalse(self.app.retry_run(self.sid)["ok"])

    def test_old_limited_task_resumes_without_limits_and_keeps_counts(self):
        self.app.send_message(self.sid, "失败后继续验收")
        failed = self.wait("error")
        session = self.app._sessions[self.sid]
        session["status"] = "limited"
        session["usage"].update(limits={"max_calls": 2, "max_tokens": 1}, warning=True)
        self.app._save(session)
        self.app.shutdown()
        self.app = DesktopService(self.root, self.root / "sessions", worker_target=retry_worker)
        self.assertEqual(self.app.get_session(self.sid)["data"]["failure"]["kind"], "legacy_limit")
        self.assertTrue(self.app.retry_run(self.sid)["ok"])
        done = self.wait("completed")
        self.assertEqual(done["usage"]["calls"], 4)
        self.assertEqual(done["usage"]["run_id"], failed["usage"]["run_id"])
        self.assertNotIn("limits", done["usage"])
        self.assertNotIn("warning", done["usage"])

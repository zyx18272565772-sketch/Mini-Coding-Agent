"""真实桌面工作进程、流式 SDK 和系统命令；模型网络全部模拟。"""

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from desktop_app.service import DesktopService
from tests.streaming_support import model_response


def background_offline_worker(connection, payload):
    from anthropic import Anthropic
    import httpx2
    from desktop_app.worker import run_worker

    query = payload["messages"][-1]["content"]
    child_task = "子任务" in query
    denied = "拒绝" in query
    stop = "停止" in query
    failure = "失败" in query
    sequence = {"main": 0, "subagent": 0}
    code = ("import os, time; from pathlib import Path; "
            "Path('background.pid').write_text(str(os.getpid())); "
            f"time.sleep({30 if stop else 2}); "
            "print('background-ok'); "
            f"raise SystemExit({3 if failure else 0})")
    command = "del protected.txt" if denied else subprocess.list2cmdline([sys.executable, "-c", code])

    def respond(request):
        body = json.loads(request.content)
        if "tools" not in body:
            return model_response([{"type": "text", "text": "[]"}], stream=False)
        scope = "subagent" if "你正在执行主智能体委派的子任务" in body["system"] else "main"
        sequence[scope] += 1
        assert sequence[scope] <= 3, "等待后台完成时不能反复请求模型"
        if not query.startswith("后台执行"):
            assert "background-ok" in json.dumps(body["messages"]), "恢复会话必须保留上一轮后台结果"
            content = [{"type": "text", "text": "上一轮后台结果已保留。"}]
        elif child_task and scope == "main":
            content = ([{"type": "tool_use", "id": "delegate", "name": "task", "input": {"prompt": "后台运行测试并返回结论"}}]
                       if sequence[scope] == 1 else [{"type": "text", "text": "已收到子任务结论。"}])
        elif sequence[scope] == 1:
            content = [{"type": "tool_use", "id": "bg_shell", "name": "bash", "input": {"command": command, "run_in_background": True}}]
        elif any("<task_notification>" in block.get("text", "") for message in body["messages"]
                 if isinstance(message["content"], list) for block in message["content"]):
            content = [{"type": "text", "text": "已根据后台实际结果确认：" + ("命令失败，退出码 3。" if failure else "background-ok。") }]
        else:
            content = [{"type": "text", "text": "权限已拒绝。" if denied else "独立工作已完成，等待后台测试结果。"}]
        return model_response(content, stream=body.get("stream", False))

    with Anthropic(api_key="test-key", base_url="https://example.test", max_retries=0,
                   http_client=httpx2.Client(transport=httpx2.MockTransport(respond))) as model, \
            patch("anthropic.Anthropic", return_value=model), patch("dotenv.load_dotenv"), \
            patch.dict(os.environ, {"MODEL_ID": "test-model", "ANTHROPIC_API_KEY": "test-key"}):
        run_worker(connection, payload)


class DesktopBackgroundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = DesktopService(self.root, self.root / "sessions", worker_target=background_offline_worker)
        self.sid = self.app.create_session()["data"]["id"]

    def tearDown(self):
        self.app.shutdown()
        self.temp.cleanup()

    def wait_for(self, condition):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            session = self.app.get_session(self.sid)["data"]
            if condition(session):
                return session
            if session["status"] == "error":
                self.fail(str(session["events"]))
            time.sleep(0.03)
        self.fail(f"未出现预期状态：{session['status']}，{session['events']}")

    def test_completion_automatically_resumes_model_and_persists_result_for_next_turn(self):
        self.assertTrue(self.app.send_message(self.sid, "后台执行验收")["ok"])
        waiting = self.wait_for(lambda item: any(event["type"] == "notice" and "等待后台" in event.get("text", "") for event in item["events"]))
        self.assertEqual(waiting["status"], "running")
        self.assertFalse(any(event["type"] == "background_result" for event in waiting["events"]))
        done = self.wait_for(lambda item: item["status"] == "completed")
        completed = [event for event in done["events"] if event["type"] == "background_result"]
        self.assertEqual(len(completed), 1)
        self.assertEqual((completed[0]["status"], completed[0]["exit_code"]), ("completed", 0))
        self.assertEqual(sum(event["type"] == "user" for event in done["events"]), 1, "无需用户再发消息唤醒")
        saved = json.loads((self.root / "sessions" / f"{self.sid}.json").read_text(encoding="utf-8"))
        tool_results = [block for message in saved["messages"] if isinstance(message["content"], list)
                        for block in message["content"] if block["type"] == "tool_result"]
        self.assertEqual([block["tool_use_id"] for block in tool_results], ["bg_shell"])
        self.assertIn("<task_notification>", json.dumps(saved["messages"]))
        self.app.shutdown()
        self.app = DesktopService(self.root, self.root / "sessions", worker_target=background_offline_worker)
        self.app.send_message(self.sid, "确认上一轮结果")
        resumed = self.wait_for(lambda item: item["status"] == "completed")
        self.assertTrue(any(event["type"] == "assistant" and "上一轮后台结果已保留" in event["text"] for event in resumed["events"]))

    def test_child_background_events_are_visible_but_details_do_not_replace_parent_history(self):
        self.app.send_message(self.sid, "后台执行子任务验收")
        done = self.wait_for(lambda item: item["status"] == "completed")
        result = next(event for event in done["events"] if event["type"] == "background_result")
        self.assertEqual(result["scope"], "subagent")
        saved = json.loads((self.root / "sessions" / f"{self.sid}.json").read_text(encoding="utf-8"))
        self.assertNotIn("<task_notification>", json.dumps(saved["messages"]))
        self.assertNotIn("bg_shell", json.dumps(saved["messages"]))
        self.assertIn("delegate", json.dumps(saved["messages"]))

    def test_background_permission_denial_does_not_delete_file(self):
        protected = self.root / "protected.txt"
        protected.write_text("保留", encoding="utf-8")
        self.app.send_message(self.sid, "后台执行拒绝验收")
        waiting = self.wait_for(lambda item: item["status"] == "waiting")
        self.assertTrue(waiting["pending_approval"]["args"]["run_in_background"])
        self.app.resolve_permission(self.sid, waiting["pending_approval"]["request_id"], False)
        done = self.wait_for(lambda item: item["status"] == "completed")
        self.assertEqual(protected.read_text(encoding="utf-8"), "保留")
        self.assertFalse(any(event["type"] == "background_result" for event in done["events"]))

    def test_stop_while_waiting_terminates_real_background_command_process(self):
        self.app.send_message(self.sid, "后台执行等待停止")
        self.wait_for(lambda item: (self.root / "background.pid").is_file() and any(
            event["type"] == "notice" and "等待后台" in event.get("text", "") for event in item["events"]))
        pid = int((self.root / "background.pid").read_text())
        handle = None
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x100000, False, pid)
            self.assertTrue(handle)
        try:
            self.assertTrue(self.app.stop(self.sid)["ok"])
            stopped = self.wait_for(lambda item: item["status"] == "stopped")
            self.assertFalse(any(event["type"] == "background_result" for event in stopped["events"]))
            if handle:
                self.assertEqual(kernel.WaitForSingleObject(handle, 2000), 0)
            else:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
        finally:
            if handle:
                kernel.CloseHandle(handle)


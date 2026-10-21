"""真实 Agent + 真实 SDK + 模拟网络，验证桌面进程、目录与恢复上下文。"""

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from desktop_app.service import DesktopService
from tests.streaming_support import model_response


def offline_worker(connection, payload):
    from anthropic import Anthropic
    import httpx2
    from desktop_app.worker import run_worker

    def respond(request):
        body = json.loads(request.content)
        if "tools" not in body:
            return httpx2.Response(200, json={
                "id": "memory", "type": "message", "role": "assistant", "model": "test-model",
                "content": [{"type": "text", "text": "[]"}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })
        assert payload["workdir"] in body["system"], "系统提示必须使用当前会话目录"
        previous_tools = [block.get("name") for message in body["messages"]
                          if isinstance(message.get("content"), list)
                          for block in message["content"] if block.get("type") == "tool_use"]
        child = "你正在执行主智能体委派的子任务" in body["system"]
        if child:
            content = [{"type": "text", "text": "仅返回子任务结论"}] if previous_tools else [
                {"type": "tool_use", "id": "child_read", "name": "read_file", "input": {"path": "desktop.txt"}}]
        elif "task" in previous_tools:
            content = [{"type": "text", "text": "已完成 " + payload["workdir"]}]
        elif "write_file" in previous_tools:
            content = [{"type": "tool_use", "id": "parent_task", "name": "task", "input": {"prompt": "读取 desktop.txt 并给出结论"}}]
        else:
            content = [{"type": "tool_use", "id": "parent_write", "name": "write_file", "input": {
                "path": "desktop.txt", "content": "本会话文件：" + payload["workdir"],
            }}]
        return model_response(content, stream=body.get("stream", False))

    with Anthropic(api_key="test-key", base_url="https://example.test", max_retries=0,
                   http_client=httpx2.Client(transport=httpx2.MockTransport(respond))) as model, \
            patch("anthropic.Anthropic", return_value=model), patch("dotenv.load_dotenv"), \
            patch.dict(os.environ, {"MODEL_ID": "test-model", "ANTHROPIC_API_KEY": "test-key"}):
        run_worker(connection, payload)


def memory_offline_worker(connection, payload):
    from anthropic import Anthropic
    import httpx2
    from desktop_app.worker import run_worker
    from tests.test_memory_store import candidate

    query = payload["messages"][-1]["content"]

    def respond(request):
        body = json.loads(request.content)
        if "tools" in body:
            if query.startswith("记住"):
                text = "已了解你的注释偏好。"
            else:
                assert "用户希望代码注释使用中文。" in body["system"], "新进程必须召回项目记忆"
                assert len(body["messages"]) == 1, "新会话不能复制旧会话历史"
                text = "你希望代码注释使用中文。"
        elif "你负责选择" in body["system"]:
            text = "[0]"
        elif query.startswith("记住"):
            text = json.dumps([candidate(body="用户希望代码注释使用中文。")], ensure_ascii=False)
        else:
            text = "[]"
        return model_response([{"type": "text", "text": text}], stream=body.get("stream", False))

    with Anthropic(api_key="test-key", base_url="https://example.test", max_retries=0,
                   http_client=httpx2.Client(transport=httpx2.MockTransport(respond))) as model, \
            patch("anthropic.Anthropic", return_value=model), patch("dotenv.load_dotenv"), \
            patch.dict(os.environ, {"MODEL_ID": "test-model", "ANTHROPIC_API_KEY": "test-key"}):
        run_worker(connection, payload)


class DesktopWorkerTests(unittest.TestCase):
    def test_new_session_after_restart_recalls_memory_from_project_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DesktopService(root, root / "sessions", worker_target=memory_offline_worker)

            def wait_for_completion(session_id):
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    session = app.get_session(session_id)["data"]
                    if session["status"] not in ("running", "waiting"):
                        self.assertEqual(session["status"], "completed", session["events"])
                        return session
                    time.sleep(0.03)
                self.fail("记忆测试任务未及时完成")

            try:
                first = app.create_session()["data"]["id"]
                app.send_message(first, "记住：以后代码注释请使用中文。")
                wait_for_completion(first)
                self.assertTrue((root / ".memory" / "chinese.md").is_file())
                app.shutdown()
                app = DesktopService(root, root / "sessions", worker_target=memory_offline_worker)
                second = app.create_session()["data"]["id"]
                app.send_message(second, "我的代码注释偏好是什么？")
                session = wait_for_completion(second)
                self.assertTrue(any(event["type"] == "notice" and "已召回" in event["text"] for event in session["events"]))
                self.assertEqual(sum(event["type"] == "user" for event in session["events"]), 1)
            finally:
                app.shutdown()

    def test_parallel_workspaces_subagent_visibility_and_resumed_sdk_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DesktopService(root, root / "sessions", worker_target=offline_worker)
            ids = []
            try:
                for name in ("project-a", "project-b"):
                    workdir = root / name
                    workdir.mkdir()
                    sid = app.create_session()["data"]["id"]

                    class Picker:
                        def create_file_dialog(self, *args, **kwargs):
                            return (str(workdir),)

                    app._window = Picker()
                    self.assertTrue(app.choose_workspace(sid)["ok"])
                    self.assertTrue(app.send_message(sid, "创建文件并用子任务检查")["ok"])
                    ids.append(sid)
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    if all(app.get_session(sid)["data"]["status"] not in ("running", "waiting") for sid in ids):
                        break
                    time.sleep(0.03)
                for sid, name in zip(ids, ("project-a", "project-b")):
                    session = app.get_session(sid)["data"]
                    self.assertEqual(session["status"], "completed", session["events"])
                    expected = str((root / name).resolve())
                    self.assertEqual((root / name / "desktop.txt").read_text(encoding="utf-8"), "本会话文件：" + expected)
                    child_reads = [event for event in session["events"] if event["type"] == "tool_start" and event["scope"] == "subagent"]
                    self.assertEqual([event["name"] for event in child_reads], ["read_file"])
                    saved = json.loads((root / "sessions" / f"{sid}.json").read_text(encoding="utf-8"))
                    result_ids = [block["tool_use_id"] for message in saved["messages"] if isinstance(message["content"], list)
                                  for block in message["content"] if block["type"] == "tool_result"]
                    self.assertEqual(result_ids, ["parent_write", "parent_task"])
                app.shutdown()
                app = DesktopService(root, root / "sessions", worker_target=offline_worker)
                self.assertTrue(app.send_message(ids[0], "继续检查")["ok"])
                deadline = time.monotonic() + 8
                while app.get_session(ids[0])["data"]["status"] == "running" and time.monotonic() < deadline:
                    time.sleep(0.03)
                resumed = app.get_session(ids[0])["data"]
                self.assertEqual(resumed["status"], "completed", resumed["events"])
                self.assertEqual(sum(event["type"] == "user" for event in resumed["events"]), 2)
            finally:
                app.shutdown()


if __name__ == "__main__":
    unittest.main()

"""项目共享任务、旧会话兼容，以及桌面工作进程的领取者身份。"""

import json
import os
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch

from desktop_app.service import DesktopService
from desktop_app.store import SessionStore
from task_system.store import TaskStore
from tests.streaming_support import model_response


def task_offline_worker(connection, payload):
    from anthropic import Anthropic
    import httpx2
    from desktop_app.worker import run_worker

    def call(call_id, name, **arguments):
        return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}

    step, task_id = 0, None
    query = payload["messages"][-1]["content"]

    def respond(request):
        nonlocal step, task_id
        body = json.loads(request.content)
        if "tools" not in body:
            return model_response([{"type": "text", "text": "[]"}], stream=False)
        step += 1
        assert "todo_write" not in {item["name"] for item in body["tools"]}
        if query.startswith("创建"):
            if step == 1:
                content = [call("create", "create_task", subject="实现设备查询接口")]
            elif step == 2:
                task_id = json.loads(body["messages"][-1]["content"][0]["content"])["id"]
                content = [call("claim", "claim_task", task_id=task_id)]
            else:
                content = [{"type": "text", "text": "已领取，稍后继续"}]
        elif step == 1:
            task_id = re.search(r"task_[0-9a-f]{8}", query).group()
            content = [call("complete", "complete_task", task_id=task_id)]
        else:
            content = [{"type": "text", "text": body["messages"][-1]["content"][0]["content"]}]
        return model_response(content, stream=body.get("stream", False))

    with Anthropic(api_key="test-key", base_url="https://example.test", max_retries=0,
                   http_client=httpx2.Client(transport=httpx2.MockTransport(respond))) as model, \
            patch("anthropic.Anthropic", return_value=model), patch("dotenv.load_dotenv"), \
            patch.dict(os.environ, {"MODEL_ID": "test-model", "ANTHROPIC_API_KEY": "test-key"}):
        run_worker(connection, payload)


class DesktopTaskTests(unittest.TestCase):
    def test_project_tasks_shared_across_sessions_restored_and_not_copied_to_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DesktopService(root, root / "sessions")
            first, second = [app.create_session()["data"]["id"] for _ in range(2)]
            task = TaskStore(root).create("项目共享任务")
            for sid in (first, second):
                self.assertEqual(app.get_session(sid)["data"]["tasks"][0]["id"], task.id)
                self.assertEqual(app.poll(sid)["data"]["tasks"][0]["status"], "pending")
            saved = json.loads((root / "sessions" / f"{first}.json").read_text(encoding="utf-8"))
            self.assertNotIn("tasks", saved)
            self.assertNotIn("todos", saved)
            app.shutdown()
            restored = DesktopService(root, root / "sessions")
            self.assertEqual(restored.get_session(first)["data"]["tasks"][0]["id"], task.id)
            other = root / "other"
            other.mkdir()
            restored._sessions[second]["workdir"] = str(other)
            self.assertEqual(restored.get_session(second)["data"]["tasks"], [])
            self.assertFalse((other / ".tasks").exists())
            restored.shutdown()

    def test_corrupt_task_file_does_not_prevent_loading_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DesktopService(root, root / "sessions")
            sid = app.create_session()["data"]["id"]
            task = TaskStore(root).create("任务")
            path = root / ".tasks" / f"{task.id}.json"
            path.write_text("{损坏", encoding="utf-8")
            view = app.get_session(sid)
            self.assertTrue(view["ok"])
            self.assertIn("任务", view["data"]["task_error"])
            self.assertEqual(path.read_text(encoding="utf-8"), "{损坏")
            app.shutdown()

    def test_legacy_todos_preserved_as_history_without_restoring_old_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = SessionStore(root / "sessions")
            session = store.create(root)
            session["todos"] = [{"content": "旧计划", "status": "in_progress"}]
            store.save(session)
            app = DesktopService(root, root / "sessions")
            view = app.get_session(session["id"])["data"]
            self.assertEqual(view["tasks"], [])
            self.assertEqual(app._sessions[session["id"]]["todos"], session["todos"])
            self.assertNotIn("todos", view)
            app.shutdown()

    def test_worker_uses_stable_session_owner_after_restart_and_rejects_other_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DesktopService(root, root / "sessions", worker_target=task_offline_worker)

            def send(sid, text):
                self.assertTrue(app.send_message(sid, text)["ok"])
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    view = app.get_session(sid)["data"]
                    if view["status"] not in ("running", "waiting"):
                        self.assertEqual(view["status"], "completed", view["events"])
                        return view
                    time.sleep(0.03)
                self.fail("桌面任务测试超时")

            try:
                first, second = [app.create_session()["data"]["id"] for _ in range(2)]
                send(first, "创建并领取任务")
                task = TaskStore(root).list()[0]
                self.assertEqual(task.owner, first)
                denied = send(second, f"完成任务 {task.id}")
                self.assertTrue(any("其他会话领取" in event.get("output", "") for event in denied["events"]))
                self.assertEqual(TaskStore(root).load(task.id).status, "in_progress")
                app.shutdown()
                app = DesktopService(root, root / "sessions", worker_target=task_offline_worker)
                send(first, f"完成任务 {task.id}")
                self.assertEqual(TaskStore(root).load(task.id).status, "completed")
            finally:
                app.shutdown()


if __name__ == "__main__":
    unittest.main()

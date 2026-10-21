"""跨进程测试桌面接口；使用模拟工作进程，不访问模型 API。"""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import unittest

from desktop_app.service import DesktopService


def simulated_worker(connection, payload):
    if payload.get("active_request", "").startswith("失败后继续"):
        from tests.test_desktop_retry import retry_worker
        return retry_worker(connection, payload)
    if payload.get("active_request", "").startswith("用量验收"):
        from tests.test_desktop_usage import usage_worker
        return usage_worker(connection, payload)
    query = payload["messages"][-1]["content"]
    if query.startswith("后台执行"):
        from tests.test_desktop_background import background_offline_worker
        return background_offline_worker(connection, payload)
    answer = "完成"
    if query == "任务系统验收":
        from dataclasses import asdict
        import json
        from task_system.store import TaskStore

        store = TaskStore(Path(payload["workdir"]))
        owner = payload["session_id"]
        sequence = 0

        def action(name, args, handler):
            nonlocal sequence
            sequence += 1
            call_id = f"task_check_{sequence}"
            connection.send({"type": "tool_start", "scope": "main", "call_id": call_id, "name": name, "args": args})
            result = handler()
            output = json.dumps(asdict(result), ensure_ascii=False) if hasattr(result, "__dataclass_fields__") else str(result)
            connection.send({"type": "tool_result", "scope": "main", "call_id": call_id, "name": name, "output": output, "allowed": True})
            return result

        first = action("create_task", {"subject": "实现设备查询接口"}, lambda: store.create("实现设备查询接口", "按设备编号返回设备信息"))
        second = action("create_task", {"subject": "验证设备查询接口"}, lambda: store.create("验证设备查询接口"))
        action("update_task", {"task_id": second.id, "addBlockedBy": [first.id]}, lambda: store.update_dependencies(second.id, [first.id]))
        action("claim_task", {"task_id": first.id}, lambda: store.claim(first.id, owner))
        time.sleep(3)
        action("complete_task", {"task_id": first.id}, lambda: store.complete(first.id, owner))
        action("claim_task", {"task_id": second.id}, lambda: store.claim(second.id, owner))
        action("complete_task", {"task_id": second.id}, lambda: store.complete(second.id, owner))
        answer = "设备查询接口及验证任务均已完成。"
        connection.send({"type": "assistant", "scope": "main", "text": answer})
    elif query.startswith("外观验收"):
        connection.send({"type": "tool_start", "scope": "main", "call_id": "read_demo", "name": "read_file", "args": {"path": "main.py"}})
        connection.send({"type": "tool_result", "scope": "main", "call_id": "read_demo", "name": "read_file", "output": "def main():\n    return 0", "allowed": True})
        answer = ("已完成项目检查，入口和工具调用流程正常。\n\n### 修改说明\n\n"
                  "- 主循环按顺序执行工具，并将结果交回模型。\n"
                  "- `main.py` 负责接收用户任务，保留当前会话。\n\n"
                  "| 模块 | 状态 |\n| --- | --- |\n| Agent Loop | 已验证 |\n| 工具调用 | 已验证 |\n\n"
                  "```python\ndef main():\n    message = \"你好，开始新的任务\"\n    return message\n```\n\n"
                  "可以选中这段说明，继续追问具体的实现细节。")
        connection.send({"type": "assistant", "scope": "main", "text": answer})
    elif query.startswith("流式输出"):
        connection.send({"type": "assistant_start", "scope": "main", "message_id": "stream_demo"})
        chunks = ["正在审查项目。\n\n", "- 已读取入口文件。\n", "- 正在检查异常处理。\n\n", "**审查完成。**"]
        for index, chunk in enumerate(chunks):
            connection.send({"type": "assistant_delta", "scope": "main", "message_id": "stream_demo", "text": chunk})
            if "等待停止" in query and index == 0:
                time.sleep(30)
            time.sleep(0.6)
        answer = "".join(chunks)
        connection.send({"type": "assistant", "scope": "main", "message_id": "stream_demo", "text": answer})
    elif query == "等待审批":
        connection.send({"type": "tool_start", "scope": "main", "call_id": "call_1", "name": "bash", "args": {"command": "del test.txt"}})
        connection.send({"type": "permission", "scope": "main", "request_id": "approval_1", "tool": "bash", "args": {"command": "del test.txt"}, "reason": "删除文件"})
        answer = connection.recv()
        output = "已允许" if answer["allow"] else "权限被拒绝。"
        connection.send({"type": "tool_result", "scope": "main", "call_id": "call_1", "name": "bash", "output": output, "allowed": answer["allow"]})
    elif query == "一直运行":
        connection.send({"type": "tool_start", "scope": "main", "call_id": "slow", "name": "bash", "args": {"command": "等待"}})
        time.sleep(30)
    elif query == "启动命令子进程":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        connection.send({"type": "tool_start", "scope": "main", "call_id": "slow", "name": "bash", "args": {"child_pid": child.pid}})
        child.wait()
    else:
        output = f"工作目录：{payload['workdir']}，历史消息：{len(payload['messages'])}"
        connection.send({"type": "assistant", "scope": "main", "text": output})
    messages = payload["messages"] + [{"role": "assistant", "content": [{"type": "text", "text": answer}]}]
    connection.send({"type": "checkpoint", "messages": messages})
    connection.send({"type": "done"})
    connection.close()


class DesktopServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.app = DesktopService(self.root, self.root / "sessions", worker_target=simulated_worker)
        self.sid = self.app.create_session()["data"]["id"]

    def tearDown(self):
        self.app.shutdown()
        self.temp.cleanup()

    def wait_status(self, expected):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            data = self.app.get_session(self.sid)["data"]
            if data["status"] == expected:
                return data
            time.sleep(0.03)
        self.fail(f"没有进入 {expected} 状态，当前为 {data['status']}")

    def test_permission_waits_rejects_stale_answer_and_can_deny(self):
        self.assertTrue(self.app.send_message(self.sid, "等待审批")["ok"])
        waiting = self.wait_status("waiting")
        self.assertEqual(waiting["pending_approval"]["request_id"], "approval_1")
        self.assertFalse(self.app.resolve_permission(self.sid, "旧请求", True)["ok"])
        self.assertFalse(self.app.send_message(self.sid, "不应重复启动")["ok"])
        self.assertTrue(self.app.resolve_permission(self.sid, "approval_1", False)["ok"])
        done = self.wait_status("completed")
        result = next(event for event in done["events"] if event["type"] == "tool_result")
        self.assertFalse(result["allowed"])
        self.assertIsNone(done["pending_approval"])

    def test_stop_during_permission_does_not_allow_operation(self):
        self.app.send_message(self.sid, "等待审批")
        self.wait_status("waiting")
        self.assertTrue(self.app.stop(self.sid)["ok"])
        stopped = self.wait_status("stopped")
        self.assertIsNone(stopped["pending_approval"])
        self.assertFalse(self.app.resolve_permission(self.sid, "approval_1", True)["ok"])
        self.assertFalse(any(event["type"] == "tool_result" for event in stopped["events"]))

    def test_can_allow_exact_pending_permission(self):
        self.app.send_message(self.sid, "等待审批")
        self.wait_status("waiting")
        self.assertTrue(self.app.resolve_permission(self.sid, "approval_1", True)["ok"])
        done = self.wait_status("completed")
        result = next(event for event in done["events"] if event["type"] == "tool_result")
        self.assertTrue(result["allowed"])

    @unittest.skipUnless(os.name == "nt", "验证 Windows 命令进程树停止")
    def test_stop_terminates_command_child_process_too(self):
        import ctypes
        self.app.send_message(self.sid, "启动命令子进程")
        deadline = time.monotonic() + 8
        child_pid = None
        while time.monotonic() < deadline:
            events = self.app.get_session(self.sid)["data"]["events"]
            child_pid = next((event["args"]["child_pid"] for event in events if event["type"] == "tool_start"), None)
            if child_pid:
                break
            time.sleep(0.03)
        self.assertIsNotNone(child_pid)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, child_pid)
        try:
            self.assertTrue(handle)
            self.assertEqual(kernel.WaitForSingleObject(handle, 0), 258)
            self.assertTrue(self.app.stop(self.sid)["ok"])
            self.wait_status("stopped")
            self.assertEqual(kernel.WaitForSingleObject(handle, 2000), 0)
        finally:
            if handle:
                kernel.CloseHandle(handle)

    def test_restart_keeps_context_and_event_cursor_does_not_duplicate(self):
        self.app.send_message(self.sid, "第一次")
        first = self.wait_status("completed")
        cursor = first["events"][-1]["seq"]
        self.assertEqual(self.app.poll(self.sid, cursor)["data"]["events"], [])
        self.app.shutdown()
        self.app = DesktopService(self.root, self.root / "sessions", worker_target=simulated_worker)
        self.app.send_message(self.sid, "第二次")
        second = self.wait_status("completed")
        self.assertIn("历史消息：3", next(event["text"] for event in reversed(second["events"]) if event["type"] == "assistant"))
        self.assertEqual(sum(event["type"] == "user" for event in second["events"]), 2)

    def test_workspace_cannot_change_while_running(self):
        self.app.send_message(self.sid, "一直运行")
        self.assertFalse(self.app.choose_workspace(self.sid)["ok"])
        self.assertTrue(self.app.stop(self.sid)["ok"])
        self.wait_status("stopped")

    def test_invalid_inputs_return_consistent_errors(self):
        self.assertFalse(self.app.send_message(self.sid, "  ")["ok"])
        self.assertFalse(self.app.get_session("../x")["ok"])
        self.assertFalse(self.app.poll(self.sid, -1)["ok"])


if __name__ == "__main__":
    unittest.main()

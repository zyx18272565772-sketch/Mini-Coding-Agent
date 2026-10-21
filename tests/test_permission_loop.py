"""使用临时文件，通过实际主循环验证权限审批。"""

import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from anthropic.types import Message

from tool import workspace
from tool.registry import TOOL_HANDLERS
from tests.memory_support import isolate_memory

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), \
        patch("dotenv.load_dotenv"):
    from agent import agentloop as agent

import main


def response(content):
    return Message(
        id="msg_test", type="message", role="assistant", model="test-model",
        content=content, stop_reason="end_turn", usage={"input_tokens": 1, "output_tokens": 1},
    )


def call(call_id, name, **arguments):
    return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}


class PermissionLoopTests(unittest.TestCase):
    def setUp(self):
        isolate_memory(self, agent)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.workdir = self.root / "workspace"
        self.workdir.mkdir()
        self.client = Mock()
        for patcher in (
            patch.object(workspace, "WORKDIR", self.workdir),
            patch.object(agent, "client", self.client),
            patch("sys.stdout", io.StringIO()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_calls(self, calls):
        self.client.messages.create.side_effect = [
            response(calls), response([{"type": "text", "text": "完成"}]),
        ]
        messages = [{"role": "user", "content": "执行工具"}]
        agent.agent_loop(messages, messages[-1]["content"])
        return messages[2]["content"]

    def test_relative_and_absolute_outside_calls_are_denied_before_handlers_run(self):
        outside = self.root / "outside.txt"
        outside.write_text("unchanged", encoding="utf-8")
        calls = []
        for path in ("../outside.txt", str(outside)):
            calls.extend([
                call(f"read_{len(calls)}", "read_file", path=path),
                call(f"write_{len(calls)}", "write_file", path=path, content="changed"),
                call(f"edit_{len(calls)}", "edit_file", path=path, old_text="unchanged", new_text="changed"),
            ])
        handlers = {name: Mock(wraps=TOOL_HANDLERS[name])
                    for name in ("read_file", "write_file", "edit_file")}
        with patch.dict(agent.TOOL_HANDLERS, handlers), patch("builtins.input", return_value="n") as prompt:
            results = self.run_calls(calls)
        self.assertEqual(prompt.call_count, 6)
        self.assertEqual([item["tool_use_id"] for item in results], [item["id"] for item in calls])
        self.assertTrue(all(item["content"] == "权限被拒绝。" for item in results))
        for handler in handlers.values():
            handler.assert_not_called()
        self.assertEqual(outside.read_text(encoding="utf-8"), "unchanged")

    def test_explicit_approval_allows_outside_write_edit_and_read(self):
        path = "../approved/nested/demo.txt"
        calls = [
            call("write", "write_file", path=path, content="original"),
            call("edit", "edit_file", path=path, old_text="original", new_text="edited"),
            call("read", "read_file", path=path),
        ]
        with patch("builtins.input", return_value="y") as prompt:
            results = self.run_calls(calls)
        self.assertEqual(prompt.call_count, 3)
        self.assertTrue(results[0]["content"].startswith("已写入"))
        self.assertTrue(results[1]["content"].startswith("已修改"))
        self.assertEqual(results[2]["content"], "edited")
        self.assertEqual((self.root / "approved/nested/demo.txt").read_text(encoding="utf-8"), "edited")

    def test_hard_denied_call_does_not_prevent_later_allowed_call(self):
        run = Mock(return_value="safe output")
        with patch.dict(agent.TOOL_HANDLERS, {"bash": run}), patch("builtins.input") as prompt:
            results = self.run_calls([
                call("blocked", "bash", command="shutdown"),
                call("allowed", "bash", command="echo safe"),
            ])
        run.assert_called_once_with(command="echo safe")
        prompt.assert_not_called()
        self.assertEqual(results[0]["content"], "权限被拒绝。")
        self.assertEqual(results[1]["content"], "safe output")

    def test_approved_call_does_not_authorize_later_call(self):
        with patch("builtins.input", side_effect=["y", "n"]):
            results = self.run_calls([
                call("first", "write_file", path="../outside.txt", content="approved"),
                call("second", "write_file", path="../outside.txt", content="not approved"),
            ])
        self.assertTrue(results[0]["content"].startswith("已写入"))
        self.assertEqual(results[1]["content"], "权限被拒绝。")
        self.assertEqual((self.root / "outside.txt").read_text(encoding="utf-8"), "approved")

    def test_end_of_input_denies_without_creating_file(self):
        with patch("builtins.input", side_effect=EOFError):
            results = self.run_calls([call("write", "write_file", path="../outside.txt", content="blocked")])
        self.assertEqual(results[0]["content"], "权限被拒绝。")
        self.assertFalse((self.root / "outside.txt").exists())

    def test_cli_reads_approval_separately_from_task_and_exit(self):
        self.client.messages.create.side_effect = [
            response([call("write", "write_file", path="../outside.txt", content="blocked")]),
            response([{"type": "text", "text": "已拒绝执行"}]),
        ]
        with patch("builtins.input", side_effect=["写入文件", "n", "exit"]) as prompt:
            self.assertEqual(main.main(), 0)
        self.assertEqual(prompt.call_count, 3)
        self.assertFalse((self.root / "outside.txt").exists())


if __name__ == "__main__":
    unittest.main()

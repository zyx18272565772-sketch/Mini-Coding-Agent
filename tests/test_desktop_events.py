"""确认原有工具、权限和消息配对可以交给桌面界面观察。"""

import copy
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from agent import events
from tool.executor import execute_tool
from tool import workspace
from tests.memory_support import isolate_memory

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), patch("dotenv.load_dotenv"):
    from agent import agentloop


class DesktopEventsTests(unittest.TestCase):
    def setUp(self):
        isolate_memory(self, agentloop)

    def tearDown(self):
        events.configure()

    def test_desktop_denial_uses_original_permission_without_terminal_input(self):
        recorded = []
        decisions = []

        def approve(name, args, reason, scope):
            decisions.append((name, args, reason, scope))
            return "deny"

        events.configure(recorded.append, approve)
        block = SimpleNamespace(id="delete", name="bash", input={"command": "del demo.txt"})
        with patch("builtins.input", side_effect=AssertionError("桌面不能读取终端")):
            result, allowed = execute_tool(block, {"bash": lambda **_: self.fail("被拒绝的命令不能执行")})
        self.assertFalse(allowed)
        self.assertEqual(result, "权限被拒绝。")
        self.assertEqual(decisions[0][0], "bash")
        self.assertEqual([event["type"] for event in recorded], ["tool_start", "tool_result"])
        self.assertFalse(recorded[-1]["allowed"])

    def test_hard_denial_never_opens_approval(self):
        events.configure(lambda event: None, lambda *args: self.fail("硬拒绝不能审批放行"))
        block = SimpleNamespace(id="danger", name="bash", input={"command": "shutdown /s"})
        output, allowed = execute_tool(block, {"bash": lambda **_: self.fail("禁止执行")})
        self.assertFalse(allowed)

    def test_subagent_events_have_separate_scope_and_restore_parent_scope(self):
        recorded = []
        events.configure(recorded.append)
        with events.subagent_scope():
            execute_tool(SimpleNamespace(id="child", name="glob", input={}), {"glob": lambda: "a.py"})
        events.emit("assistant", text="主智能体结论")
        self.assertEqual([event["scope"] for event in recorded], ["subagent", "subagent", "main"])

    def test_checkpoints_only_contain_complete_tool_batches_and_final_answer(self):
        from anthropic.types import TextBlock, ToolUseBlock
        response = [
            SimpleNamespace(content=[ToolUseBlock(type="tool_use", id="write", name="write_file", input={"path": "a.txt", "content": "内容"})]),
            SimpleNamespace(content=[TextBlock(type="text", text="已完成")]),
        ]
        recorded = []
        events.configure(lambda event: recorded.append(copy.deepcopy(event)))
        with tempfile.TemporaryDirectory() as directory, patch.object(workspace, "WORKDIR", Path(directory)), \
                patch.object(agentloop.client.messages, "create", side_effect=response):
            messages = [{"role": "user", "content": "创建文件"}]
            agentloop.agent_loop(messages, "创建文件")
        checkpoints = [event["messages"] for event in recorded if event["type"] == "checkpoint"]
        self.assertEqual(len(checkpoints), 2)
        self.assertEqual(checkpoints[0][-1]["content"][0]["tool_use_id"], "write")
        self.assertEqual(checkpoints[1][-1]["content"][0].text, "已完成")
        self.assertEqual([event["text"] for event in recorded if event["type"] == "assistant"], ["已完成"])


if __name__ == "__main__":
    unittest.main()

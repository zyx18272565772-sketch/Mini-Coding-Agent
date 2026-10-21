"""验证子智能体的独立消息、工具范围、权限钩子与持续调用。"""

import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from anthropic.types import Message, TextBlock, ToolUseBlock

import hook
from tool import workspace

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), \
        patch("dotenv.load_dotenv"):
    from agent import subagent


def response(content):
    return Message(
        id="msg_child", type="message", role="assistant", model="test-model", content=content,
        stop_reason="tool_use" if any(item["type"] == "tool_use" for item in content) else "end_turn",
        usage={"input_tokens": 1, "output_tokens": 1},
    )


def tool(call_id, name, **arguments):
    return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}


class SubagentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.workdir = self.root / "workspace"
        self.workdir.mkdir()
        self.client = Mock()
        self.received = []
        self.output = io.StringIO()
        for patcher in (
            patch.object(subagent, "client", self.client),
            patch.object(workspace, "WORKDIR", self.workdir),
            patch.dict(hook.HOOKS, {event: list(callbacks) for event, callbacks in hook.HOOKS.items()}, clear=True),
            patch("sys.stdout", self.output),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_replies(self, replies, prompt="检查模块并返回结论"):
        pending = iter(response(content) for content in replies)

        def create(**kwargs):
            self.received.append({**kwargs, "messages": list(kwargs["messages"])})
            return next(pending)

        self.client.messages.create.side_effect = create
        return subagent.run_subagent(prompt)

    def test_starts_with_only_the_delegated_prompt_and_shared_tools(self):
        result = self.run_replies([[{"type": "text", "text": "检查完成"}]])
        self.assertEqual(result, "检查完成")
        self.assertEqual(self.received[0]["messages"], [{"role": "user", "content": "检查模块并返回结论"}])
        expected = {"bash", "read_file", "write_file", "edit_file", "glob", "load_skill", "compact"}
        self.assertEqual({item["name"] for item in self.received[0]["tools"]}, expected)
        self.assertEqual(set(subagent.SUB_HANDLERS), expected)
        self.assertEqual(self.received[0]["system"], subagent.SUB_SYSTEM)
        self.assertIn("子智能体开始", self.output.getvalue())
        self.assertIn("子智能体完成", self.output.getvalue())

    def test_separate_invocations_do_not_reuse_previous_messages(self):
        self.run_replies([[{"type": "text", "text": "第一项完成"}]], prompt="任务一")
        self.run_replies([[{"type": "text", "text": "第二项完成"}]], prompt="任务二")
        self.assertEqual(self.received[1]["messages"], [{"role": "user", "content": "任务二"}])

    def test_returns_only_final_text_and_keeps_intermediate_results_local(self):
        (self.workdir / "details.txt").write_text("大量中间细节", encoding="utf-8")
        result = self.run_replies([
            [{"type": "text", "text": "先阅读文件"}, tool("read", "read_file", path="details.txt")],
            [{"type": "text", "text": "最终结论"}, {"type": "text", "text": "相关文件：details.txt"}],
        ])
        self.assertEqual(result, "最终结论\n相关文件：details.txt")
        self.assertEqual(self.received[1]["messages"][-1], {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "read", "content": "大量中间细节",
        }]})

    def test_writes_and_reads_in_the_shared_workspace_with_lifecycle_hooks(self):
        events = []
        hook.register_hook("PreToolUse", lambda block: events.append(("pre", block.id)))
        hook.register_hook("PostToolUse", lambda block, output: events.append(("post", block.id)))
        hook.register_hook("Stop", lambda messages: events.append(("stop", len(messages))))
        result = self.run_replies([
            [tool("write", "write_file", path="nested/demo.txt", content="子任务写入的内容"),
             tool("read", "read_file", path="nested/demo.txt")],
            [{"type": "text", "text": "文件已创建并检查"}],
        ])
        self.assertEqual(result, "文件已创建并检查")
        self.assertEqual((self.workdir / "nested/demo.txt").read_text(encoding="utf-8"), "子任务写入的内容")
        self.assertEqual(self.received[1]["messages"][-1]["content"][1]["content"], "子任务写入的内容")
        self.assertEqual(events, [("pre", "write"), ("post", "write"), ("pre", "read"), ("post", "read"),
                                  ("stop", 4)])

    def test_outside_write_uses_existing_approval_and_denial_skips_post_hook(self):
        outside = self.root / "outside.txt"
        outside.write_text("原内容", encoding="utf-8")
        completed = []
        hook.register_hook("PostToolUse", lambda block, output: completed.append(block.id))
        with patch("builtins.input", return_value="n") as approval:
            self.run_replies([
                [tool("outside", "write_file", path="../outside.txt", content="新内容")],
                [{"type": "text", "text": "已尊重拒绝决定"}],
            ])
        approval.assert_called_once()
        self.assertEqual(outside.read_text(encoding="utf-8"), "原内容")
        self.assertEqual(self.received[1]["messages"][-1]["content"][0]["content"], "权限被拒绝。")
        self.assertEqual(completed, [])

    def test_dangerous_command_is_blocked_before_shell_execution(self):
        with patch("tool.bash.subprocess.run") as shell, patch("builtins.input") as approval:
            self.run_replies([
                [tool("danger", "bash", command="shutdown")],
                [{"type": "text", "text": "命令被拒绝"}],
            ])
        shell.assert_not_called()
        approval.assert_not_called()
        self.assertEqual(self.received[1]["messages"][-1]["content"][0]["content"], "权限被拒绝。")

    def test_delegation_and_project_task_tools_are_unavailable_to_the_child(self):
        for name, arguments in (("task", {"prompt": "继续委派"}), ("create_task", {"subject": "不应创建"}),
                                ("claim_task", {"task_id": "task_deadbeef"}), ("complete_task", {"task_id": "task_deadbeef"})):
            with self.subTest(name=name):
                self.run_replies([
                    [tool("unsupported", name, **arguments)],
                    [{"type": "text", "text": "使用基础工具继续工作"}],
                ])
                self.assertEqual(self.received[-1]["messages"][-1]["content"][0]["content"], f"未知工具：{name}")

    def test_stop_hook_can_request_another_child_turn(self):
        hook.register_hook("Stop", Mock(side_effect=["请再核对一次", None]))
        result = self.run_replies([
            [{"type": "text", "text": "初步结论"}], [{"type": "text", "text": "核对后的结论"}],
        ])
        self.assertEqual(result, "核对后的结论")
        self.assertEqual(self.received[1]["messages"][-1], {"role": "user", "content": "请再核对一次"})

    def test_empty_final_reply_returns_a_chinese_placeholder(self):
        self.assertEqual(self.run_replies([[]]), "（子智能体未返回总结）")

    def test_child_continues_past_thirty_model_calls(self):
        self.client.messages.create.side_effect = [response([tool(f"step-{index}", "bash", command="echo test")]) for index in range(31)] + [response([{"type": "text", "text": "检查完成"}])]
        with patch.dict(subagent.SUB_HANDLERS, {"bash": Mock(return_value="继续工作")}):
            result = subagent.run_subagent("持续检查任务")
        self.assertEqual(self.client.messages.create.call_count, 32)
        self.assertEqual(result, "检查完成")

    def test_model_failure_propagates_without_a_fake_summary(self):
        self.client.messages.create.side_effect = RuntimeError("模拟模型请求失败")
        with self.assertRaisesRegex(RuntimeError, "模拟模型请求失败"):
            subagent.run_subagent("检查文件")


class ExtractTextTests(unittest.TestCase):
    def test_extracts_text_blocks_and_ignores_tool_calls(self):
        content = [TextBlock(type="text", text="结论一"),
                   ToolUseBlock(type="tool_use", id="read", name="read_file", input={"path": "demo.txt"}),
                   TextBlock(type="text", text="结论二")]
        self.assertEqual(subagent.extract_text(content), "结论一\n结论二")

    def test_empty_content_and_plain_text(self):
        self.assertEqual(subagent.extract_text([]), "")
        self.assertEqual(subagent.extract_text("直接返回文本"), "直接返回文本")


if __name__ == "__main__":
    unittest.main()

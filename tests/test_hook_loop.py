"""通过实际主循环验证钩子的执行流程。"""

import io
import os
import unittest
from unittest.mock import Mock, patch

from anthropic.types import Message

import hook
from tests.memory_support import isolate_memory

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), \
        patch("dotenv.load_dotenv"):
    from agent import agentloop as agent


def response(content):
    return Message(
        id="msg_test", type="message", role="assistant", model="test-model", content=content,
        stop_reason="end_turn", usage={"input_tokens": 1, "output_tokens": 1},
    )


def tool(call_id):
    return {"type": "tool_use", "id": call_id, "name": "bash", "input": {"command": f"echo {call_id}"}}


class HookLoopTests(unittest.TestCase):
    def setUp(self):
        isolate_memory(self, agent)
        self.client = Mock()
        self.run = Mock(return_value="tool output")
        for patcher in (
            patch.dict(hook.HOOKS, {event: list(callbacks) for event, callbacks in hook.HOOKS.items()}, clear=True),
            patch.object(agent, "client", self.client),
            patch.dict(agent.TOOL_HANDLERS, {"bash": self.run}),
            patch("sys.stdout", io.StringIO()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_tools(self, calls):
        self.client.messages.create.side_effect = [
            response(calls), response([{"type": "text", "text": "完成"}]),
        ]
        messages = [{"role": "user", "content": "执行工具"}]
        agent.agent_loop(messages, messages[-1]["content"])
        return messages

    def test_pre_execution_post_and_stop_run_in_order(self):
        events = []
        hook.register_hook("PreToolUse", lambda block: events.append("pre"))
        hook.register_hook("PostToolUse", lambda block, output: events.append(("post", output)))
        hook.register_hook("Stop", lambda messages: events.append("stop"))

        def execute(**arguments):
            events.append("execute")
            return "tool output"

        self.run.side_effect = execute
        messages = self.run_tools([tool("test")])

        self.assertEqual(events, ["pre", "execute", ("post", "tool output"), "stop"])
        self.assertEqual(messages[2]["content"][0]["content"], "tool output")

    def test_custom_pre_hook_blocks_one_call_without_post_hook_or_execution(self):
        completed = []
        hook.register_hook("PreToolUse", lambda block: "Custom denial" if block.id == "blocked" else None)
        hook.register_hook("PostToolUse", lambda block, output: completed.append(block.id))

        messages = self.run_tools([tool("blocked"), tool("allowed")])

        self.run.assert_called_once_with(command="echo allowed")
        self.assertEqual(completed, ["allowed"])
        self.assertEqual(messages[2]["content"], [
            {"type": "tool_result", "tool_use_id": "blocked", "content": "Custom denial"},
            {"type": "tool_result", "tool_use_id": "allowed", "content": "tool output"},
        ])

    def test_falsey_non_none_pre_hook_results_still_block_execution(self):
        for result in ("", False):
            with self.subTest(result=result), \
                    patch.dict(hook.HOOKS, {"PreToolUse": [lambda block: result]}):
                messages = self.run_tools([tool("blocked")])
                self.assertEqual(messages[2]["content"][0]["content"], str(result))
        self.run.assert_not_called()

    def test_post_hook_return_value_does_not_replace_tool_output(self):
        post = Mock(return_value="replacement")
        hook.register_hook("PostToolUse", post)
        messages = self.run_tools([tool("test")])
        post.assert_called_once()
        self.assertEqual(post.call_args.args[1], "tool output")
        self.assertEqual(messages[2]["content"][0]["content"], "tool output")

    def test_stop_hook_can_inject_followup_and_continue_model_loop(self):
        followup = Mock(side_effect=["请再检查一次", None])
        hook.register_hook("Stop", followup)
        self.client.messages.create.side_effect = [
            response([{"type": "text", "text": "初步完成"}]),
            response([{"type": "text", "text": "确认完成"}]),
        ]
        messages = [{"role": "user", "content": "执行任务"}]

        agent.agent_loop(messages, messages[-1]["content"])

        self.assertEqual(self.client.messages.create.call_count, 2)
        self.assertEqual(messages[2], {"role": "user", "content": "请再检查一次"})
        self.assertEqual(messages[-1]["content"][0].text, "确认完成")
        self.run.assert_not_called()

    def test_pre_hook_failure_never_executes_tool(self):
        hook.register_hook("PreToolUse", Mock(side_effect=RuntimeError("hook failed")))
        with self.assertRaisesRegex(RuntimeError, "hook failed"):
            self.run_tools([tool("test")])
        self.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

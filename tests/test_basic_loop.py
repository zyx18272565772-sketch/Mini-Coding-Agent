"""在不调用真实模型的情况下验证基本主循环。"""

import os
import subprocess
import unittest
from unittest.mock import Mock, patch

from anthropic.types import Message

from tool import bash
from tests.memory_support import isolate_memory

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), \
        patch("dotenv.load_dotenv"):
    from agent import agentloop as agent


def response(content):
    return Message(
        id="msg_test", type="message", role="assistant", model="test-model",
        content=content, stop_reason="end_turn",
        usage={"input_tokens": 1, "output_tokens": 1},
    )


def tool(call_id, command):
    return {"type": "tool_use", "id": call_id, "name": "bash", "input": {"command": command}}


class AgentLoopTests(unittest.TestCase):
    def setUp(self):
        isolate_memory(self, agent)
        self.client = Mock()
        self.patcher = patch.object(agent, "client", self.client, create=True)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_text_response_is_saved_and_loop_returns(self):
        reply = response([{"type": "text", "text": "完成"}])
        self.client.messages.create.return_value = reply
        messages = [{"role": "user", "content": "你好"}]

        self.assertIsNone(agent.agent_loop(messages, messages[-1]["content"]))

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1], {"role": "assistant", "content": reply.content})
        self.client.messages.create.assert_called_once()

    def test_all_tool_results_are_sent_back_before_next_model_call(self):
        replies = iter([
            response([tool("first", "echo one"), tool("second", "echo two")]),
            response([tool("third", "echo three")]),
            response([{"type": "text", "text": "完成"}]),
        ])
        received = []

        def create(**kwargs):
            received.append(list(kwargs["messages"]))
            return next(replies)

        self.client.messages.create.side_effect = create
        messages = [{"role": "user", "content": "执行命令"}]
        run = Mock(side_effect=["one", "two", "three"])
        with patch.dict(agent.TOOL_HANDLERS, {"bash": run}):
            agent.agent_loop(messages, messages[-1]["content"])

        self.assertEqual([m["role"] for m in messages],
                         ["user", "assistant", "user", "assistant", "user", "assistant"])
        self.assertEqual(received[1][-1], {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "first", "content": "one"},
            {"type": "tool_result", "tool_use_id": "second", "content": "two"},
        ]})
        self.assertEqual(received[2][-1]["content"][0]["tool_use_id"], "third")
        self.assertEqual([call.kwargs["command"] for call in run.call_args_list],
                         ["echo one", "echo two", "echo three"])

    def test_tool_error_text_is_fed_back_to_model(self):
        self.client.messages.create.side_effect = [
            response([tool("failed", "bad command")]),
            response([{"type": "text", "text": "命令失败"}]),
        ]
        messages = [{"role": "user", "content": "执行"}]
        with patch.dict(agent.TOOL_HANDLERS, {"bash": Mock(return_value="错误：命令执行超时（120 秒）")}):
            agent.agent_loop(messages, messages[-1]["content"])
        self.assertEqual(messages[2]["content"][0]["content"], "错误：命令执行超时（120 秒）")
        self.assertEqual(self.client.messages.create.call_count, 2)

    def test_unknown_tool_is_returned_to_model_without_stopping_loop(self):
        self.client.messages.create.side_effect = [
            response([{"type": "tool_use", "id": "unknown", "name": "missing_tool", "input": {}}]),
            response([{"type": "text", "text": "完成"}]),
        ]
        messages = [{"role": "user", "content": "执行"}]
        agent.agent_loop(messages, messages[-1]["content"])
        self.assertIn("missing_tool", messages[2]["content"][0]["content"])
        self.assertEqual(self.client.messages.create.call_count, 2)

    def test_api_failure_propagates_without_adding_fake_reply(self):
        self.client.messages.create.side_effect = RuntimeError("API failed")
        messages = [{"role": "user", "content": "执行"}]
        with self.assertRaisesRegex(RuntimeError, "API failed"):
            agent.agent_loop(messages, messages[-1]["content"])
        self.assertEqual(len(messages), 1)


class BashTests(unittest.TestCase):
    def test_real_shell_returns_command_output(self):
        self.assertEqual(bash.run_bash("echo agent-loop-ok"), "agent-loop-ok")

    @patch("tool.bash.subprocess.Popen")
    def test_reference_dangerous_command_is_blocked(self, run):
        self.assertIn("已阻止", bash.run_bash("shutdown"))
        run.assert_not_called()

    @patch("tool.bash.COMMAND_TIMEOUT", 0)
    @patch("tool.bash.subprocess.Popen")
    def test_timeout_is_returned_as_text(self, run):
        run.return_value.poll.return_value = 0
        self.assertEqual(bash.run_bash("echo test"), "错误：命令执行超时（0 秒）")

    @patch("tool.bash.subprocess.Popen", side_effect=OSError("shell unavailable"))
    def test_os_error_is_returned_as_text(self, run):
        self.assertIn("shell unavailable", bash.run_bash("echo test"))


if __name__ == "__main__":
    unittest.main()

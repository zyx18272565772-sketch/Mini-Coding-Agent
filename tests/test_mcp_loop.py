"""验证 MCP 工具进入主/子循环、权限钩子、模型消息和桌面事件。"""

import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from anthropic.types import Message
from agent import events
from mcp_client.config import GitHubConfig
from mcp_client.runtime import github_mcp_scope
from tests.test_mcp_client import FakeSession
from tests.memory_support import isolate_memory
from tool.executor import execute_tool

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), patch("dotenv.load_dotenv"):
    from agent import agentloop as parent
    from agent import subagent


def response(content):
    return Message(id="msg_mcp", type="message", role="assistant", model="test-model",
                   content=content, stop_reason="end_turn", usage={"input_tokens": 1, "output_tokens": 1})


def call():
    return {"type": "tool_use", "id": "github_read", "name": "mcp__github__list_issues",
            "input": {"owner": "someone", "repo": "demo"}}


class MCPLoopTests(unittest.TestCase):
    def setUp(self):
        isolate_memory(self, parent)
        self.session = FakeSession()
        self.config = GitHubConfig(token="local-test-secret", tools=("list_issues",))
        self.addCleanup(events.configure)

    def test_both_loops_send_mcp_schema_and_result_with_matching_call_id(self):
        for module in (parent, subagent):
            with self.subTest(agent=module.__name__):
                requests = []

                def create(**body):
                    requests.append(body)
                    self.assertIn("mcp__github__list_issues", {item["name"] for item in body["tools"]})
                    if len(requests) == 1:
                        return response([call()])
                    result = body["messages"][-1]["content"][0]
                    self.assertEqual(result["tool_use_id"], "github_read")
                    self.assertIn("远程结果", result["content"])
                    return response([{"type": "text", "text": "已读取远程 Issue"}])

                model = Mock()
                model.messages.create.side_effect = create
                with github_mcp_scope(config=self.config, connector=self.session.connect), \
                        patch.object(module, "client", model), patch("sys.stdout", io.StringIO()):
                    if module is parent:
                        messages = [{"role": "user", "content": "读取 GitHub Issue"}]
                        parent.agent_loop(messages, messages[0]["content"])
                    else:
                        self.assertEqual(subagent.run_subagent("读取 GitHub Issue"), "已读取远程 Issue")
                self.assertEqual(len(requests), 2)

    def test_remote_failure_has_is_error_in_messages_and_events(self):
        self.session.is_error = True
        self.session.text = "远程权限不足"
        model = Mock()
        model.messages.create.side_effect = [response([call()]), response([{"type": "text", "text": "读取失败"}])]
        recorded = []
        events.configure(recorded.append)
        with github_mcp_scope(config=self.config, connector=self.session.connect), \
                patch.object(parent, "client", model), patch("sys.stdout", io.StringIO()):
            messages = [{"role": "user", "content": "读取 Issue"}]
            parent.agent_loop(messages, messages[0]["content"])
        self.assertTrue(messages[2]["content"][0]["is_error"])
        tool_event = next(event for event in recorded if event["type"] == "tool_result")
        self.assertTrue(tool_event["is_error"])
        self.assertTrue(tool_event["allowed"])

    def test_unregistered_or_write_mcp_call_is_blocked_before_handler(self):
        for name in ("mcp__github__create_pull_request", "mcp__unknown__read"):
            handler = Mock()
            with github_mcp_scope(config=self.config, connector=self.session.connect), patch("sys.stdout", io.StringIO()):
                output, allowed = execute_tool(SimpleNamespace(name=name, id="forbidden", input={}), {name: handler})
            self.assertFalse(allowed)
            handler.assert_not_called()
            self.assertEqual(output, "权限被拒绝。")


if __name__ == "__main__":
    unittest.main()

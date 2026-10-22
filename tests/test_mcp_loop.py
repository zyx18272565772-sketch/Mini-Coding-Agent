"""验证 MCP 工具进入主/子循环、权限钩子、模型消息和桌面事件。"""

import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from anthropic.types import Message
import hook
from agent import events
from mcp_client.config import GitHubConfig
from mcp_client.runtime import github_mcp_scope
from tests.test_mcp_lazy import CountingSession
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


def load_call():
    return {"type": "tool_use", "id": "load_github", "name": "load_mcp", "input": {"server": "github"}}


class MCPLoopTests(unittest.TestCase):
    def setUp(self):
        isolate_memory(self, parent)
        self.session = CountingSession()
        self.config = GitHubConfig(token="local-test-secret", tools=("list_issues",))
        self.addCleanup(events.configure)

    def test_both_loops_send_mcp_schema_and_result_with_matching_call_id(self):
        for module in (parent, subagent):
            with self.subTest(agent=module.__name__):
                self.session = CountingSession()
                requests = []

                def create(**body):
                    requests.append(body)
                    names = {item["name"] for item in body["tools"]}
                    if len(requests) == 1:
                        self.assertIn("load_mcp", names)
                        self.assertNotIn("mcp__github__list_issues", names)
                        self.assertEqual(self.session.connections, 0)
                        return response([load_call()])
                    self.assertIn("mcp__github__list_issues", names)
                    if len(requests) == 2:
                        self.assertEqual(body["messages"][-1]["content"][0]["tool_use_id"], "load_github")
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
                self.assertEqual(len(requests), 3)
                self.assertEqual((self.session.connections, self.session.discoveries), (1, 1))

    def test_remote_failure_has_is_error_in_messages_and_events(self):
        self.session.is_error = True
        self.session.text = "远程权限不足"
        model = Mock()
        model.messages.create.side_effect = [response([load_call()]), response([call()]), response([{"type": "text", "text": "读取失败"}])]
        recorded = []
        events.configure(recorded.append)
        with github_mcp_scope(config=self.config, connector=self.session.connect), \
                patch.object(parent, "client", model), patch("sys.stdout", io.StringIO()):
            messages = [{"role": "user", "content": "读取 Issue"}]
            parent.agent_loop(messages, messages[0]["content"])
        self.assertTrue(messages[4]["content"][0]["is_error"])
        tool_event = next(event for event in recorded if event["type"] == "tool_result" and event["name"] == "mcp__github__list_issues")
        self.assertTrue(tool_event["is_error"])
        self.assertTrue(tool_event["allowed"])

    def test_unregistered_or_write_mcp_call_is_blocked_before_handler(self):
        for name in ("mcp__github__create_pull_request", "mcp__unknown__read"):
            handler = Mock()
            with github_mcp_scope(config=self.config, connector=self.session.connect) as runtime, patch("sys.stdout", io.StringIO()):
                runtime.handlers["load_mcp"](server="github")
                output, allowed = execute_tool(SimpleNamespace(name=name, id="forbidden", input={}), {name: handler})
            self.assertFalse(allowed)
            handler.assert_not_called()
            self.assertEqual(output, "权限被拒绝。")

    def test_plain_and_concept_questions_do_not_connect_even_when_enabled(self):
        for module in (parent, subagent):
            for query in ("解释本地代码", "GitHub MCP 是什么", "继续刚才的本地任务"):
                with self.subTest(agent=module.__name__, query=query):
                    self.session = CountingSession()
                    model = Mock()
                    model.messages.create.return_value = response([{"type": "text", "text": "普通回答"}])
                    recorded = []
                    events.configure(recorded.append)
                    with patch.dict(os.environ, {"GITHUB_MCP_ENABLED": "true", "GITHUB_MCP_TOKEN": self.config.token}), \
                            patch("mcp_client.runtime.open_github_session", self.session.connect), \
                            patch.object(module, "client", model), patch("sys.stdout", io.StringIO()):
                        if module is parent:
                            module.agent_loop([{ "role": "user", "content": query}], query)
                        else:
                            self.assertEqual(module.run_subagent(query), "普通回答")
                    self.assertEqual(model.messages.create.call_count, 1)
                    names = {item["name"] for item in model.messages.create.call_args.kwargs["tools"]}
                    self.assertIn("load_mcp", names)
                    self.assertFalse(any(name.startswith("mcp__") for name in names))
                    self.assertEqual((self.session.connections, self.session.discoveries), (0, 0))
                    self.assertFalse(any("GitHub MCP" in event.get("text", "") for event in recorded))

    def test_child_first_load_is_available_to_parent_without_reconnecting(self):
        requests = []

        def create(**body):
            requests.append(body)
            step = len(requests)
            names = {item["name"] for item in body["tools"]}
            if step == 1:
                self.assertIn("load_mcp", names)
                return response([{"type": "tool_use", "id": "delegate", "name": "task", "input": {"prompt": "查询 GitHub Issue"}}])
            if step == 2:
                self.assertEqual(self.session.connections, 0)
                return response([load_call()])
            self.assertIn("mcp__github__list_issues", names)
            self.assertFalse(self.session.closed)
            if step in (3, 5):
                return response([call()])
            return response([{"type": "text", "text": "子任务结论" if step == 4 else "父任务完成"}])

        model = Mock()
        model.messages.create.side_effect = create
        with github_mcp_scope(config=self.config, connector=self.session.connect), \
                patch.object(parent, "client", model), patch.object(subagent, "client", model), patch("sys.stdout", io.StringIO()):
            messages = [{"role": "user", "content": "委派查询 GitHub，再补查一个 Issue"}]
            parent.agent_loop(messages, messages[0]["content"])
        self.assertEqual(len(requests), 6)
        self.assertEqual((self.session.connections, self.session.discoveries), (1, 1))
        self.assertEqual(len(self.session.calls), 2)
        self.assertTrue(self.session.closed)

    def test_loaded_handlers_are_available_to_later_calls_in_same_batch(self):
        model = Mock()
        model.messages.create.side_effect = [response([load_call(), call()]), response([{"type": "text", "text": "完成"}])]
        with github_mcp_scope(config=self.config, connector=self.session.connect), \
                patch.object(parent, "client", model), patch("sys.stdout", io.StringIO()):
            messages = [{"role": "user", "content": "查询 GitHub Issue"}]
            parent.agent_loop(messages, messages[0]["content"])
        self.assertEqual([block["tool_use_id"] for block in messages[2]["content"]], ["load_github", "github_read"])
        self.assertIn("远程结果", messages[2]["content"][1]["content"])
        self.assertEqual(len(self.session.calls), 1)

    def test_pre_hook_denial_of_loader_never_opens_a_connection(self):
        model = Mock()
        model.messages.create.side_effect = [response([load_call()]), response([{"type": "text", "text": "已尊重拒绝"}])]
        with patch.dict(hook.HOOKS, {event: list(callbacks) for event, callbacks in hook.HOOKS.items()}, clear=True), \
                github_mcp_scope(config=self.config, connector=self.session.connect), \
                patch.object(parent, "client", model), patch("sys.stdout", io.StringIO()):
            hook.register_hook("PreToolUse", lambda block: "加载被拒绝" if block.name == "load_mcp" else None)
            messages = [{"role": "user", "content": "查询 GitHub Issue"}]
            parent.agent_loop(messages, messages[0]["content"])
        self.assertEqual(messages[2]["content"][0]["content"], "加载被拒绝")
        self.assertEqual((self.session.connections, self.session.discoveries), (0, 0))


if __name__ == "__main__":
    unittest.main()

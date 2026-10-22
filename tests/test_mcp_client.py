"""使用模拟 MCP 会话验证发现、过滤、调用和资源关闭，不连接 GitHub。"""

import asyncio
from contextlib import asynccontextmanager
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from mcp_client.config import GitHubConfig, load_github_config
from mcp_client.runtime import current_mcp, github_mcp_scope


def remote_tool(name="list_issues"):
    return SimpleNamespace(name=name, input_schema={"type": "object", "properties": {
        "owner": {"type": "string"}, "repo": {"type": "string"}}, "required": ["owner", "repo"]})


class FakeSession:
    def __init__(self, tools=None):
        self.tools = tools or [remote_tool()]
        self.calls = []
        self.closed = False
        self.is_error = False
        self.text = "远程结果"

    async def list_tools(self, *, cursor=None):
        return SimpleNamespace(tools=self.tools, next_cursor=None)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return SimpleNamespace(is_error=self.is_error, structured_content=None,
                               content=[SimpleNamespace(type="text", text=self.text)])

    @asynccontextmanager
    async def connect(self, config):
        try:
            yield self
        finally:
            self.closed = True


class MCPConfigTests(unittest.TestCase):
    def test_disabled_by_default_and_token_never_appears_in_config_repr(self):
        self.assertIsNone(load_github_config({}))
        self.assertIsNone(load_github_config({"GITHUB_MCP_TOKEN": "private-value"}))
        config = load_github_config({"GITHUB_MCP_ENABLED": "true", "GITHUB_MCP_TOKEN": "private-value"})
        self.assertNotIn("private-value", repr(config))

    def test_missing_token_invalid_switch_and_write_tool_are_rejected(self):
        for environment in ({"GITHUB_MCP_ENABLED": "true"}, {"GITHUB_MCP_ENABLED": "wrong"},
                            {"GITHUB_MCP_ENABLED": "true", "GITHUB_MCP_TOKEN": "test", "GITHUB_MCP_TOOLS": "create_pull_request"}):
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                load_github_config(environment)


class MCPRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.config = GitHubConfig(token="private-test-token", tools=("list_issues",))
        self.session = FakeSession([remote_tool(), remote_tool("create_pull_request"), remote_tool("bash")])

    def scope(self):
        return github_mcp_scope(config=self.config, connector=self.session.connect)

    def test_discovers_only_allowed_tools_maps_schema_and_closes_session(self):
        with self.scope() as runtime:
            self.assertIs(current_mcp(), runtime)
            self.assertEqual([tool["name"] for tool in runtime.tools], ["mcp__github__list_issues"])
            self.assertEqual(runtime.tools[0]["input_schema"]["required"], ["owner", "repo"])
            output = runtime.handlers["mcp__github__list_issues"](owner="someone", repo="demo")
            self.assertIn("远程结果", output)
            self.assertFalse(output.is_error)
        self.assertEqual(self.session.calls, [("list_issues", {"owner": "someone", "repo": "demo"})])
        self.assertTrue(self.session.closed)
        self.assertIsNone(current_mcp())

    def test_nested_subagent_scope_reuses_connection(self):
        with self.scope() as parent:
            with github_mcp_scope() as child:
                self.assertIs(child, parent)
            self.assertFalse(self.session.closed)
        self.assertTrue(self.session.closed)

    def test_invalid_arguments_never_reach_remote_server(self):
        with self.scope() as runtime:
            output = runtime.handlers["mcp__github__list_issues"](owner=123)
        self.assertTrue(output.is_error)
        self.assertEqual(self.session.calls, [])

    def test_discovers_tools_on_later_pages(self):
        async def pages(*, cursor=None):
            return SimpleNamespace(tools=[remote_tool("bash")] if cursor is None else [remote_tool()],
                                   next_cursor="second" if cursor is None else None)

        self.session.list_tools = pages
        with self.scope() as runtime:
            self.assertIn("mcp__github__list_issues", runtime.handlers)

    def test_endless_pagination_is_bounded_and_closes_connection(self):
        async def pages(*, cursor=None):
            return SimpleNamespace(tools=[], next_cursor="again")

        self.session.list_tools = pages
        with patch("mcp_client.runtime.emit"), self.scope() as runtime:
            self.assertIsNone(runtime)
        self.assertTrue(self.session.closed)

    def test_external_schema_references_are_rejected(self):
        tool = remote_tool()
        tool.input_schema["properties"]["owner"] = {"$ref": "file:///private-config"}
        self.session.tools = [tool]
        with patch("mcp_client.runtime.emit"), self.scope() as runtime:
            self.assertIsNone(runtime)
        self.assertTrue(self.session.closed)

    def test_embedded_text_resources_are_returned_without_binary_payloads(self):
        async def resource(name, arguments):
            return SimpleNamespace(is_error=False, structured_content=None, content=[
                SimpleNamespace(type="resource", resource=SimpleNamespace(text="仓库文件正文")),
                SimpleNamespace(type="image", data="private-binary-payload"),
            ])

        self.session.call_tool = resource
        with self.scope() as runtime:
            output = runtime.handlers["mcp__github__list_issues"](owner="someone", repo="demo")
        self.assertIn("仓库文件正文", output)
        self.assertNotIn("private-binary-payload", output)

    def test_server_errors_and_output_truncation_are_explicit_and_redacted(self):
        self.session.is_error = True
        self.session.text = self.config.token + "x" * 30_000
        with self.scope() as runtime:
            output = runtime.handlers["mcp__github__list_issues"](owner="someone", repo="demo")
        self.assertTrue(output.is_error)
        self.assertNotIn(self.config.token, output)
        self.assertIn("截断", output)
        self.assertLess(len(output), 21_000)

    def test_startup_failure_falls_back_without_exposing_token(self):
        @asynccontextmanager
        async def broken(config):
            raise RuntimeError(config.token)
            yield  # pragma: no cover

        with patch("mcp_client.runtime.emit") as notice:
            with github_mcp_scope(config=self.config, connector=broken) as runtime:
                self.assertIsNone(runtime)
        self.assertNotIn(self.config.token, str(notice.call_args_list))

    def test_agent_exception_propagates_and_connection_is_closed(self):
        with self.assertRaisesRegex(RuntimeError, "agent failed"):
            with self.scope():
                raise RuntimeError("agent failed")
        self.assertTrue(self.session.closed)
        self.assertIsNone(current_mcp())

    def test_remote_call_has_timeout_and_returns_error(self):
        async def slow(name, arguments):
            await asyncio.sleep(1)

        self.session.call_tool = slow
        with patch("mcp_client.runtime.CALL_TIMEOUT", 0.01), self.scope() as runtime:
            output = runtime.handlers["mcp__github__list_issues"](owner="someone", repo="demo")
        self.assertTrue(output.is_error)

    def test_disabled_scope_does_not_create_connection(self):
        with patch.dict(os.environ, {"GITHUB_MCP_ENABLED": "false"}):
            with github_mcp_scope() as runtime:
                self.assertIsNone(runtime)


if __name__ == "__main__":
    unittest.main()

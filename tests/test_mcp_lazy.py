"""按需加载的连接时机、幂等性和父子共享；不连接真实 GitHub。"""

from contextlib import asynccontextmanager
import os
import unittest
from unittest.mock import patch

from mcp_client.config import GitHubConfig
from mcp_client.runtime import current_mcp, extend_tools, github_mcp_scope
from tests.test_mcp_client import FakeSession


class CountingSession(FakeSession):
    def __init__(self):
        super().__init__()
        self.connections = self.discoveries = 0

    @asynccontextmanager
    async def connect(self, config):
        self.connections += 1
        async with super().connect(config) as session:
            yield session

    async def list_tools(self, *, cursor=None):
        self.discoveries += 1
        return await super().list_tools(cursor=cursor)


class LazyMCPTests(unittest.TestCase):
    def setUp(self):
        self.config = GitHubConfig(token="lazy-test-secret", tools=("list_issues",))
        self.session = CountingSession()

    def test_entering_enabled_scope_only_exposes_loader_and_does_not_connect(self):
        with patch("mcp_client.runtime.emit") as notice:
            with github_mcp_scope(config=self.config, connector=self.session.connect) as runtime:
                self.assertIs(current_mcp(), runtime)
                tools, handlers = extend_tools([], {}, runtime)
                self.assertEqual([tool["name"] for tool in tools], ["load_mcp"])
                self.assertEqual(set(handlers), {"load_mcp"})
                self.assertEqual((self.session.connections, self.session.discoveries), (0, 0))
            notice.assert_not_called()
        self.assertFalse(self.session.closed)
        self.assertIsNone(current_mcp())

    def test_explicit_loading_discovers_once_and_repeated_loading_reuses_it(self):
        with github_mcp_scope(config=self.config, connector=self.session.connect) as runtime:
            result = runtime.handlers["load_mcp"](server="github")
            self.assertFalse(result.is_error)
            self.assertIn("mcp__github__list_issues", result)
            self.assertIn("mcp__github__list_issues", runtime.handlers)
            self.assertNotIn("load_mcp", {tool["name"] for tool in runtime.tools})
            self.assertFalse(runtime.handlers["load_mcp"](server="github").is_error)
            self.assertEqual((self.session.connections, self.session.discoveries), (1, 1))
        self.assertTrue(self.session.closed)

    def test_child_can_load_parent_scope_without_closing_parent_connection(self):
        with github_mcp_scope(config=self.config, connector=self.session.connect) as parent:
            with github_mcp_scope() as child:
                self.assertIs(child, parent)
                self.assertFalse(child.handlers["load_mcp"](server="github").is_error)
            self.assertIn("mcp__github__list_issues", parent.handlers)
            self.assertEqual(self.session.connections, 1)
            self.assertFalse(self.session.closed)
        self.assertTrue(self.session.closed)

    def test_missing_token_is_only_reported_when_loading_is_requested(self):
        with patch.dict(os.environ, {"GITHUB_MCP_ENABLED": "true", "GITHUB_MCP_TOKEN": ""}), \
                patch("mcp_client.runtime.open_github_session") as connect, \
                patch("mcp_client.runtime.emit") as notice:
            with github_mcp_scope() as runtime:
                self.assertIsNotNone(runtime)
                notice.assert_not_called()
                result = runtime.handlers["load_mcp"](server="github")
                self.assertTrue(result.is_error)
                self.assertEqual(runtime.tools, [])
                connect.assert_not_called()

    def test_failure_is_redacted_and_not_retried_in_the_same_task(self):
        attempts = []

        @asynccontextmanager
        async def broken(config):
            attempts.append(1)
            raise RuntimeError(config.token)
            yield

        with patch("mcp_client.runtime.emit") as notice:
            with github_mcp_scope(config=self.config, connector=broken) as runtime:
                first = runtime.handlers["load_mcp"](server="github")
                second = runtime.handlers["load_mcp"](server="github")
                self.assertTrue(first.is_error and second.is_error)
                self.assertEqual(len(attempts), 1)
                self.assertEqual(runtime.tools, [])
                self.assertNotIn(self.config.token, str(first) + str(second) + str(notice.call_args_list))

    def test_unsupported_server_does_not_connect_or_consume_github_attempt(self):
        with github_mcp_scope(config=self.config, connector=self.session.connect) as runtime:
            self.assertTrue(runtime.handlers["load_mcp"](server="other").is_error)
            self.assertEqual(self.session.connections, 0)
            self.assertFalse(runtime.handlers["load_mcp"](server="github").is_error)
            self.assertEqual(self.session.connections, 1)

    def test_disabled_scope_exposes_no_loader_and_creates_no_connection(self):
        with patch.dict(os.environ, {"GITHUB_MCP_ENABLED": "false"}), \
                patch("mcp_client.runtime.open_github_session") as connect:
            with github_mcp_scope() as runtime:
                self.assertIsNone(runtime)
                self.assertEqual(extend_tools([], {}, runtime), ([], {}))
            connect.assert_not_called()

    def test_later_user_task_starts_unloaded_after_previous_connection_closed(self):
        with github_mcp_scope(config=self.config, connector=self.session.connect) as first:
            self.assertFalse(first.handlers["load_mcp"](server="github").is_error)
        self.assertTrue(self.session.closed)
        with github_mcp_scope(config=self.config, connector=self.session.connect) as later:
            self.assertIsNot(first, later)
            self.assertEqual([tool["name"] for tool in later.tools], ["load_mcp"])
            self.assertEqual((self.session.connections, self.session.discoveries), (1, 1))


if __name__ == "__main__":
    unittest.main()

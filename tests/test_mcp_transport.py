"""真实官方 SDK + 内存 HTTP 传输，验证 MCP 握手、认证头与工具往返。"""

import json
import unittest
from unittest.mock import patch

import httpx2

from mcp_client.config import ENDPOINT, GitHubConfig
from mcp_client.runtime import github_mcp_scope


class MCPTransportTests(unittest.TestCase):
    def test_official_sdk_initializes_lists_and_calls_over_streamable_http(self):
        requests = []
        clients = []
        native_client = httpx2.AsyncClient
        config = GitHubConfig(token="wire-test-secret", tools=("list_issues",))

        def respond(request):
            self.assertEqual(str(request.url), ENDPOINT)
            self.assertEqual(request.headers["Authorization"], "Bearer wire-test-secret")
            self.assertEqual(request.headers["X-MCP-Readonly"], "true")
            self.assertEqual(request.headers["X-MCP-Tools"], "list_issues")
            if request.method != "POST":
                return httpx2.Response(405)
            body = json.loads(request.content)
            requests.append(body)
            method = body["method"]
            if method == "notifications/initialized":
                return httpx2.Response(202)
            if method == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "fake-github", "version": "1.0"}}
            elif method == "tools/list":
                result = {"tools": [{"name": "list_issues", "inputSchema": {
                    "type": "object", "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}},
                    "required": ["owner", "repo"]}}]}
            elif method == "tools/call":
                self.assertEqual(body["params"]["name"], "list_issues")
                self.assertEqual(body["params"]["arguments"], {"owner": "someone", "repo": "demo"})
                result = {"content": [{"type": "text", "text": "通过真实 MCP 协议返回的 Issue"}], "isError": False}
            else:
                self.fail(f"不应发起其它请求：{method}")
            return httpx2.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

        def client_factory(**options):
            client = native_client(transport=httpx2.MockTransport(respond), **options)
            clients.append(client)
            return client

        with patch("httpx2.AsyncClient", side_effect=client_factory):
            with github_mcp_scope(config=config) as runtime:
                self.assertIsNotNone(runtime)
                output = runtime.handlers["mcp__github__list_issues"](owner="someone", repo="demo")
                self.assertIn("通过真实 MCP 协议返回的 Issue", output)
                self.assertFalse(output.is_error)
        self.assertEqual([item["method"] for item in requests],
                         ["initialize", "notifications/initialized", "tools/list", "tools/call"])
        self.assertTrue(all(client.is_closed for client in clients))

    def test_agent_error_is_preserved_when_real_sdk_closes(self):
        from llm.errors import describe_failure

        def respond(request):
            if request.method != "POST":
                return httpx2.Response(405)
            body = json.loads(request.content)
            if body["method"] == "notifications/initialized":
                return httpx2.Response(202)
            result = ({"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                       "serverInfo": {"name": "offline", "version": "1.0"}}
                      if body["method"] == "initialize" else
                      {"tools": [{"name": "list_issues", "inputSchema": {"type": "object"}}]})
            return httpx2.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

        native_client = httpx2.AsyncClient
        clients = []

        def client_factory(**options):
            client = native_client(transport=httpx2.MockTransport(respond), **options)
            clients.append(client)
            return client

        for error in (httpx2.ReadTimeout("offline timeout"), SystemExit(0)):
            with self.subTest(error=type(error).__name__), patch("httpx2.AsyncClient", side_effect=client_factory):
                try:
                    with github_mcp_scope(config=GitHubConfig(token="offline", tools=("list_issues",))):
                        raise error
                except BaseException as received:
                    self.assertIs(received, error, "SDK 清理不能将 Agent 异常包装成 ExceptionGroup")
                    if isinstance(error, Exception):
                        self.assertTrue(describe_failure(received)["retryable"])
                else:
                    self.fail("不能吞掉 Agent 原始异常")
        self.assertTrue(all(client.is_closed for client in clients))


if __name__ == "__main__":
    unittest.main()

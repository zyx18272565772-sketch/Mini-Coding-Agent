"""官方 MCP SDK 的 Streamable HTTP 会话，认证仅交给固定 GitHub 端点。"""

from contextlib import asynccontextmanager

from mcp_client.config import ENDPOINT


@asynccontextmanager
async def open_github_session(config):
    # 可选依赖按需加载，普通 Agent 使用不要求安装 MCP SDK。
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {config.token}", "X-MCP-Readonly": "true",
               "X-MCP-Tools": ",".join(config.tools)}
    async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(20, connect=5),
                                 follow_redirects=False) as http:
        transport = streamable_http_client(ENDPOINT, http_client=http)
        # initialize 握手兼容 GitHub 现有 MCP 协议，不启用模型采样和交互式回调。
        async with Client(transport, mode="legacy", read_timeout_seconds=30) as session:
            yield session

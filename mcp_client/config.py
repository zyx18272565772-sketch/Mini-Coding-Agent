"""GitHub MCP 的显式启用开关、令牌和只读工具白名单。"""

from dataclasses import dataclass, field
import os


ENDPOINT = "https://api.githubcopilot.com/mcp/readonly"
READ_TOOLS = {
    "search_repositories": "搜索 GitHub 仓库",
    "get_file_contents": "读取 GitHub 仓库文件",
    "search_code": "搜索 GitHub 代码",
    "list_issues": "列出 GitHub Issue",
    "issue_read": "读取 GitHub Issue 详情或评论",
    "list_pull_requests": "列出 GitHub PR",
    "pull_request_read": "读取 GitHub PR 详情、差异或评论",
}


@dataclass(frozen=True)
class GitHubConfig:
    token: str = field(repr=False)
    tools: tuple[str, ...] = tuple(READ_TOOLS)

    def __post_init__(self):
        if not self.token.strip() or "\n" in self.token or "\r" in self.token:
            raise ValueError("请在本地配置有效的 GITHUB_MCP_TOKEN，不要发送到聊天中")
        if not self.tools or any(name not in READ_TOOLS for name in self.tools):
            raise ValueError("GitHub MCP 只允许配置受支持的只读工具")


def load_github_config(environment=None) -> GitHubConfig | None:
    environment = os.environ if environment is None else environment
    enabled = environment.get("GITHUB_MCP_ENABLED", "false").strip().lower()
    if enabled in ("false", "0", "no", ""):
        return None
    if enabled not in ("true", "1", "yes"):
        raise ValueError("GITHUB_MCP_ENABLED 必须是 true 或 false")
    tools = tuple(dict.fromkeys(name.strip() for name in environment.get(
        "GITHUB_MCP_TOOLS", ",".join(READ_TOOLS)).split(",") if name.strip()))
    return GitHubConfig(token=environment.get("GITHUB_MCP_TOKEN", "").strip(), tools=tools)

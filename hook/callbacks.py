import json

from agent import permission
from tool import workspace


def permission_hook(block) -> str | None:
    """复用 s03 权限检查，转换为钩子的返回约定。"""
    if not permission.check_permission(block):
        return "权限被拒绝。"
    return None


def log_hook(block):
    args_preview = json.dumps(block.input, ensure_ascii=False)[:60]
    print(f"[钩子] 执行工具：{block.name}，参数：{args_preview}")
    return None


def large_output_hook(block, output):
    if len(str(output)) > 100000:
        print(f"[钩子] 工具 {block.name} 的输出较长：共 {len(str(output))} 个字符")
    return None


def context_inject_hook(query: str):
    """与示例一致，打印当前目录，不改写用户输入。"""
    print(f"[钩子] 用户提交任务，当前工作目录：{workspace.WORKDIR}")
    return None


def summary_hook(messages: list):
    tool_count = sum(
        1
        for message in messages
        for block in (message["content"] if isinstance(message.get("content"), list) else [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    )
    print(f"[钩子] 本轮结束，当前历史保留 {tool_count} 条工具结果")
    return None

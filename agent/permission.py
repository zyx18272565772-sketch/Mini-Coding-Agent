"""工具执行前的三道权限检查：硬拒绝、规则匹配、用户审批。"""

import json
import re

from agent.events import ask_approval
from tool import workspace


DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if=", "> /dev/sda"]


def check_deny_list(command: str) -> str | None:
    if not isinstance(command, str):
        raise ValueError("command 参数必须是字符串")
    for pattern in DENY_LIST:
        if pattern in command.casefold():
            return f"命令包含硬拒绝名单中的内容“{pattern}”"
    return None


DESTRUCTIVE_COMMAND_WORD = re.compile(
    r"(?i)(?:^|[;&|()\n])\s*(?:rm|del)(?=\s|$|[;&|()])"
)


def contains_destructive_command(command: str) -> bool:
    return bool(DESTRUCTIVE_COMMAND_WORD.search(command))


PERMISSION_RULES = [
    {
        "tools": ["read_file", "write_file", "edit_file"],
        "check": lambda args: not (workspace.WORKDIR / args["path"]).resolve().is_relative_to(workspace.WORKDIR),
        "message": "访问工作目录之外的文件",
    },
    {
        "tools": ["search_files"],
        "check": lambda args: not workspace.resolve_path(args.get("path", ".")).is_relative_to(workspace.WORKDIR),
        "message": "搜索工作目录之外的文件或目录",
    },
    {
        "tools": ["bash"],
        "check": lambda args: contains_destructive_command(args["command"]) or any(
            keyword in args["command"].casefold() for keyword in ["rm ", "> /etc/", "chmod 777"]
        ),
        "message": "命令可能修改或删除重要文件",
    },
]


def check_rules(tool_name: str, args: dict) -> str | None:
    for rule in PERMISSION_RULES:
        if tool_name in rule["tools"] and rule["check"](args):
            return rule["message"]
    return None


def ask_user(tool_name: str, args: dict, reason: str) -> str:
    decision = ask_approval(tool_name, args, reason)
    if decision is not None:
        return decision
    print(f"\n[权限确认] {reason}")
    print(f"   工具：{tool_name}")
    print(f"   参数：{json.dumps(args, ensure_ascii=False)}")
    try:
        choice = input("   是否允许？输入 y 或 yes 同意，直接回车拒绝 [y/N]：").strip().lower()
    except EOFError:
        return "deny"
    return "allow" if choice in ("y", "yes") else "deny"


def check_permission(block) -> bool:
    if not isinstance(block.input, dict):
        print("\n[已阻止] 工具参数必须是字典对象")
        return False
    if block.name.startswith("mcp__"):
        from mcp_client.runtime import current_mcp

        runtime = current_mcp()
        if runtime is None or block.name not in runtime.handlers:
            print("\n[已阻止] MCP 工具未连接、未注册或不在只读白名单中")
            return False
    try:
        # 硬拒绝优先，不提供审批放行的入口。
        if block.name == "bash":
            reason = check_deny_list(block.input["command"])
            if reason:
                print(f"\n[已阻止] {reason}")
                return False
        reason = check_rules(block.name, block.input)
    except (KeyError, TypeError, ValueError, OSError) as exc:
        print(f"\n[已阻止] 工具参数无效：{exc}")
        return False

    if reason:
        return ask_user(block.name, block.input, reason) == "allow"
    return True

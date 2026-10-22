"""摘要时保存项目状态，并在摘要后有界恢复近期已读取的项目文件。"""

import json
from pathlib import Path
import re

from desktop_app.store import to_plain
from storage.factory import task_store
from storage.references import is_artifact_reference


def completed_calls(messages):
    calls = {}
    for message in to_plain(messages):
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                calls[block["id"]] = block
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                text = str(block.get("content", ""))
                if not block.get("is_error") and not text.startswith("错误："):
                    yield calls[block["tool_use_id"]], text


def state_snapshot(messages, workdir, tools):
    try:
        tasks = [{"id": task.id, "subject": task.subject, "status": task.status,
                  "owner": task.owner, "blockedBy": task.blockedBy}
                 for task in task_store(workdir).list()[:50]]
    except Exception:
        tasks = "任务状态读取失败；后续可通过 list_tasks 恢复。"
    skills, used_mcp = [], []
    for call, _ in completed_calls(messages):
        if call["name"] == "load_skill":
            name = call.get("input", {}).get("name")
            if name and name not in skills:
                skills.append(name)
        if call["name"].startswith("mcp__") and call["name"] not in used_mcp:
            used_mcp.append(call["name"])
    return json.dumps({"tasks": tasks, "loaded_skills": skills[-30:], "used_mcp_tools": used_mcp[-25:],
                       "available_mcp_tools": [tool["name"] for tool in tools
                                               if tool["name"].startswith("mcp__")][:25]}, ensure_ascii=False)


def recover_recent_files(messages, workdir):
    """仅恢复成功读过的项目内普通文件；沿用原行范围和 read_file 的大小限制。"""
    from tool.read_file import run_read
    root = workdir.resolve()
    recovered, seen = [], set()
    for call, previous in reversed(list(completed_calls(messages))):
        if call["name"] != "read_file" or not (re.match(r"\d+ \|", previous) or previous.startswith("<persisted-output>\n")):
            continue
        arguments = call.get("input", {})
        raw_path = arguments.get("path", "")
        if not isinstance(raw_path, str) or is_artifact_reference(raw_path):
            continue
        try:
            path = Path(raw_path)
            path = (path if path.is_absolute() else root / path).resolve()
            if path in seen or not path.is_relative_to(root) or not path.is_file():
                continue
        except (OSError, ValueError, RuntimeError):
            continue
        seen.add(path)
        options = {key: arguments[key] for key in ("start_line", "end_line", "limit") if key in arguments}
        text = run_read(str(path), **options)
        if text.startswith("错误："):
            continue
        recovered.append({"role": "user", "content": "[压缩后恢复的文件内容，仅供参考]\n" + json.dumps(
            {"path": str(path), "start_line": options.get("start_line", 1), "content": text}, ensure_ascii=False)})
        if len(recovered) >= 5:
            break
    return recovered

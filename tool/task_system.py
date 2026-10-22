"""主智能体的六个任务工具；task 工具仍单独负责委派子智能体。"""

from dataclasses import asdict
import json

from storage.factory import task_store as TaskStore
from tool import workspace


_owner = "agent"


def configure_owner(owner: str):
    """桌面进程使用稳定的会话编号，恢复会话后仍可完成自己领取的任务。"""
    global _owner
    _owner = owner


def _json(task):
    return json.dumps(asdict(task), ensure_ascii=False, indent=2)


def run_create_task(subject: str, description: str = "") -> str:
    task = TaskStore(workspace.WORKDIR).create(subject, description)
    print(f"[任务] 已创建 {task.id}：{task.subject}")
    return _json(task)


def run_update_task(task_id: str, addBlockedBy: list[str]) -> str:
    task = TaskStore(workspace.WORKDIR).update_dependencies(task_id, addBlockedBy)
    print(f"[任务] 已更新依赖：{task.subject}")
    return _json(task)


def run_list_tasks() -> str:
    tasks = TaskStore(workspace.WORKDIR).snapshot()
    if not tasks:
        return "暂无任务。需要跟踪多步骤工作时，可用 create_task 创建任务。"
    lines = []
    for task in tasks:
        marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}[task["status"]]
        blocked = f"；等待依赖：{', '.join(task['incompleteDependencies'])}" if task["incompleteDependencies"] else ""
        owner = f"；领取者：{task['owner']}" if task["owner"] else ""
        lines.append(f"{marker} {task['id']}：{task['subject']}（{task['status']}{owner}{blocked}）")
    return "\n".join(lines)


def run_get_task(task_id: str) -> str:
    return _json(TaskStore(workspace.WORKDIR).load(task_id))


def run_claim_task(task_id: str) -> str:
    task = TaskStore(workspace.WORKDIR).claim(task_id, _owner)
    print(f"[任务] 已领取：{task.subject}")
    return _json(task)


def run_complete_task(task_id: str) -> str:
    task, unblocked = TaskStore(workspace.WORKDIR).complete(task_id, _owner)
    output = f"已完成 {task.id}：{task.subject}"
    if unblocked:
        output += "\n以下任务的依赖已满足，可以领取：\n" + "\n".join(f"{item.id}：{item.subject}" for item in unblocked)
    print(f"[任务] {output}")
    return output

"""参考 s10 的任务状态机；使用文件锁和原子替换支持桌面多会话。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import secrets
import time
from uuid import uuid4


TASK_ID_PATTERN = re.compile(r"^task_[0-9a-f]{8}$")


@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str
    owner: str | None
    blockedBy: list[str]


class TaskStore:
    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        self.directory = self.workdir / ".tasks"

    def _root(self, create=False):
        root = self.directory.resolve()
        if not root.is_relative_to(self.workdir):
            raise ValueError("任务目录不能位于工作目录之外。")
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return root

    def _path(self, task_id):
        if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
            raise ValueError(f"任务编号无效：{task_id!r}")
        root = self._root()
        path = (root / f"{task_id}.json").resolve()
        if not path.is_relative_to(root):
            raise ValueError("任务文件不能位于任务目录之外。")
        return path

    @contextmanager
    def _locked(self):
        root = self._root(create=True)
        lock_path = (root / ".lock").resolve()
        if not lock_path.is_relative_to(root):
            raise ValueError("任务锁文件不能位于任务目录之外。")
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 3
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("任务记录正在更新，请稍后重试。")
                    time.sleep(0.02)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _save(self, task):
        path = self._path(task.id)
        temporary = path.with_name(f".{task.id}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(asdict(task), ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def load(self, task_id) -> Task:
        path = self._path(task_id)
        try:
            task = Task(**json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            raise ValueError(f"任务不存在：{task_id}") from None
        except (TypeError, ValueError):
            raise ValueError(f"任务文件格式无效，原文件已保留：{path.name}") from None
        if (task.id != task_id or not isinstance(task.subject, str) or not task.subject.strip()
                or not isinstance(task.description, str)
                or task.status not in ("pending", "in_progress", "completed")
                or (task.status == "pending" and task.owner is not None)
                or (task.status != "pending" and (not isinstance(task.owner, str) or not task.owner.strip()))
                or not isinstance(task.blockedBy, list)
                or any(not isinstance(item, str) or not TASK_ID_PATTERN.fullmatch(item) for item in task.blockedBy)):
            raise ValueError(f"任务文件字段无效，原文件已保留：{path.name}")
        return task

    def _list(self):
        return [self.load(path.stem) for path in sorted(self._root().glob("task_*.json"))]

    def list(self) -> list[Task]:
        if not self._root().exists():
            return []
        with self._locked():
            return self._list()

    def create(self, subject: str, description: str = "") -> Task:
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("任务标题不能为空。")
        if not isinstance(description, str):
            raise ValueError("任务描述必须是文本。")
        with self._locked():
            for _ in range(100):
                task = Task(f"task_{secrets.token_hex(4)}", subject.strip(), description, "pending", None, [])
                if not self._path(task.id).exists():
                    self._save(task)
                    return task
        raise RuntimeError("无法生成唯一的任务编号。")

    def _depends_on(self, task_id, target_id):
        pending, visited = [task_id], set()
        while pending:
            current = pending.pop()
            if current == target_id:
                return True
            if current not in visited:
                visited.add(current)
                pending.extend(self.load(current).blockedBy)
        return False

    def update_dependencies(self, task_id: str, add_blocked_by: list[str]) -> Task:
        if (not isinstance(add_blocked_by, list)
                or any(not isinstance(item, str) or not TASK_ID_PATTERN.fullmatch(item) for item in add_blocked_by)):
            raise ValueError("依赖必须是任务编号列表。")
        with self._locked():
            task = self.load(task_id)
            if task.status != "pending" or task.owner is not None:
                raise ValueError("只能修改尚未领取的待处理任务的依赖。")
            for dependency in dict.fromkeys(add_blocked_by):
                if dependency == task_id:
                    raise ValueError("任务不能依赖自身。")
                self.load(dependency)
                if self._depends_on(dependency, task_id):
                    raise ValueError("不能添加形成循环的任务依赖。")
            task.blockedBy = list(dict.fromkeys([*task.blockedBy, *add_blocked_by]))
            self._save(task)
            return task

    def _incomplete(self, task):
        incomplete = []
        for dependency in task.blockedBy:
            try:
                done = self.load(dependency).status == "completed"
            except ValueError:
                done = False
            if not done:
                incomplete.append(dependency)
        return incomplete

    def snapshot(self) -> list[dict]:
        if not self._root().exists():
            return []
        with self._locked():
            return [{**asdict(task), "incompleteDependencies": self._incomplete(task)} for task in self._list()]

    def claim(self, task_id: str, owner: str = "agent") -> Task:
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("任务领取者不能为空。")
        # 检查与修改在同一把进程间文件锁内完成，避免重复领取。
        with self._locked():
            task = self.load(task_id)
            if task.status != "pending":
                raise ValueError(f"任务 {task_id} 当前状态为 {task.status}，不能重复领取。")
            incomplete = self._incomplete(task)
            if incomplete:
                raise ValueError(f"以下依赖尚未完成，不能领取：{', '.join(incomplete)}")
            task.owner, task.status = owner, "in_progress"
            self._save(task)
            return task

    def complete(self, task_id: str, owner: str = "agent") -> tuple[Task, list[Task]]:
        with self._locked():
            task = self.load(task_id)
            if task.status != "in_progress":
                raise ValueError("只有进行中的任务才能标记为完成。")
            if task.owner != owner:
                raise ValueError("该任务由其他会话领取，当前会话不能标记完成。")
            tasks = self._list()
            blocked_before = {item.id for item in tasks if item.status == "pending" and self._incomplete(item)}
            task.status = "completed"
            self._save(task)
            unblocked = [item for item in tasks if item.id in blocked_before and not self._incomplete(item)]
            return task, unblocked

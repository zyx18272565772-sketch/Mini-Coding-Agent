"""保留任务图的业务规则，将文件锁和 JSON 读写替换为 InnoDB 事务。"""
from dataclasses import asdict
from collections import defaultdict
import secrets

from sqlalchemy import delete, select
from storage.database import upsert, utcnow
from storage.models import project_tasks, task_dependencies
from storage.project_store import ProjectStore
from task_system.store import TaskStore, Task, TASK_ID_PATTERN


class SQLTaskStore(ProjectStore, TaskStore):
    def load(self, task_id):
        if not isinstance(task_id, str) or not TASK_ID_PATTERN.fullmatch(task_id):
            raise ValueError("任务编号无效")
        with self._locked():
            c, pid = self._scope()
            row = c.execute(select(project_tasks).where(project_tasks.c.project_id == pid,
                project_tasks.c.task_id == task_id)).mappings().first()
            if row is None:
                raise ValueError(f"任务不存在：{task_id}")
            dependencies = c.execute(select(task_dependencies.c.depends_on).where(
                task_dependencies.c.project_id == pid, task_dependencies.c.task_id == task_id)
                .order_by(task_dependencies.c.depends_on)).scalars().all()
            return Task(task_id, row["subject"], row["description"], row["status"], row["owner_session_id"], dependencies)

    def _save(self, task):
        c, pid = self._scope()
        upsert(c, project_tasks, dict(project_id=pid, task_id=task.id, subject=task.subject,
            description=task.description, status=task.status, owner_session_id=task.owner, updated_at=utcnow()), ("project_id", "task_id"))
        c.execute(delete(task_dependencies).where(task_dependencies.c.project_id == pid,
            task_dependencies.c.task_id == task.id))
        if task.blockedBy:
            c.execute(task_dependencies.insert(), [dict(project_id=pid, task_id=task.id, depends_on=d) for d in task.blockedBy])

    def _list(self):
        c, pid = self._scope()
        dependencies = defaultdict(list)
        for row in c.execute(select(task_dependencies).where(task_dependencies.c.project_id == pid)
                .order_by(task_dependencies.c.depends_on)).mappings():
            dependencies[row["task_id"]].append(row["depends_on"])
        return [Task(row["task_id"], row["subject"], row["description"], row["status"],
                     row["owner_session_id"], dependencies[row["task_id"]])
                for row in c.execute(select(project_tasks).where(project_tasks.c.project_id == pid)
                    .order_by(project_tasks.c.task_id)).mappings()]

    def list(self):
        with self._locked():
            return self._list()

    def snapshot(self):
        with self._locked():
            tasks = self._list()
            statuses = {t.id: t.status for t in tasks}
            return [{**asdict(task), "incompleteDependencies": [d for d in task.blockedBy
                     if statuses.get(d) != "completed"]} for task in tasks]

    def create(self, subject, description=""):
        if not isinstance(subject, str) or not subject.strip() or not isinstance(description, str):
            raise ValueError("任务标题不能为空，描述必须是文本。")
        with self._locked():
            c, pid = self._scope()
            for _ in range(100):
                task = Task(f"task_{secrets.token_hex(4)}", subject.strip(), description, "pending", None, [])
                if c.execute(select(project_tasks.c.task_id).where(project_tasks.c.project_id == pid,
                        project_tasks.c.task_id == task.id)).first() is None:
                    self._save(task)
                    return task
        raise RuntimeError("无法生成唯一的任务编号")

"""同项目的任务图和记忆更新使用项目行锁，不跨模型请求持锁。"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from sqlalchemy import select
from storage.models import projects
from storage.projects import ensure_project


class ProjectStore:
    def __init__(self, workdir, database):
        self.workdir = Path(workdir).resolve()
        self.database = database
        self._connection = ContextVar(f"project_connection_{id(self)}", default=None)

    @contextmanager
    def _locked(self):
        existing = self._connection.get()
        if existing is not None:
            yield
            return
        with self.database.transaction() as connection:
            project_id = ensure_project(connection, self.workdir)
            connection.execute(select(projects.c.id).where(projects.c.id == project_id).with_for_update()).one()
            token = self._connection.set((connection, project_id))
            try:
                yield
            finally:
                self._connection.reset(token)

    def _scope(self):
        value = self._connection.get()
        if value is None:
            raise RuntimeError("项目存储操作必须处于事务内")
        return value

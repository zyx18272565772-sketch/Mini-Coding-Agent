"""数据库记忆存储，沿用记忆筛选、去重与快照比较规则。"""
from uuid import uuid4

from sqlalchemy import delete, select

from memory.store import FileMemoryStore, records_from_snapshot
from storage.context import current_context
from storage.database import upsert, utcnow
from storage.models import memories
from storage.project_store import ProjectStore


class SQLMemoryStore(ProjectStore, FileMemoryStore):
    def _snapshot(self):
        c, pid = self._scope()
        return dict(c.execute(select(memories.c.filename, memories.c.document).where(
            memories.c.project_id == pid).order_by(memories.c.filename)).all())

    def snapshot(self):
        with self._locked():
            return self._snapshot()

    def read(self, filename):
        if not isinstance(filename, str) or "/" in filename or "\\" in filename or not filename.endswith(".md"):
            raise ValueError("记忆文件名无效")
        with self._locked():
            c, pid = self._scope()
            return c.execute(select(memories.c.document).where(memories.c.project_id == pid,
                memories.c.filename == filename)).scalar()

    def _commit(self, updated, previous):
        c, pid = self._scope()
        records = records_from_snapshot(updated)
        scope = current_context()
        for record in records:
            filename = record["filename"]
            if updated[filename] == previous.get(filename):
                continue
            old = c.execute(select(memories).where(memories.c.project_id == pid,
                memories.c.filename == filename)).mappings().first()
            upsert(c, memories, dict(id=old["id"] if old else uuid4().hex, project_id=pid,
                filename=filename, name=record["name"], type=record["type"], description=record["description"],
                body=record["body"], document=updated[filename], source_session_id=scope.get("session_id"),
                source_run_id=scope.get("run_id"), version=old["version"] + 1 if old else 1, updated_at=utcnow()), ("id", "project_id", "filename"))
        removed = previous.keys() - updated.keys()
        if removed:
            c.execute(delete(memories).where(memories.c.project_id == pid, memories.c.filename.in_(removed)))

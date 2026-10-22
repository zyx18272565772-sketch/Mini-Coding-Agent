"""大文件保留本地，数据库登记归属、路径、大小和校验值。"""
import hashlib
from pathlib import Path
from uuid import uuid4

from storage.config import storage_backend
from storage.context import current_context


def register_file(database, workdir, path, kind, *, scope=None, connection=None):
    from sqlalchemy import select
    from storage.database import path_key, upsert, utcnow
    from storage.models import artifacts
    from storage.projects import ensure_project
    workdir, path = Path(workdir).resolve(), Path(path).resolve()
    if not path.is_relative_to(workdir) or not path.is_file():
        raise ValueError("归档文件必须位于项目工作目录内")
    scope = scope or {}
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(block)
    def save(c):
        pid = ensure_project(c, workdir)
        key = path_key(path)
        old = c.execute(select(artifacts).where(artifacts.c.project_id == pid, artifacts.c.path_key == key)).mappings().first()
        upsert(c, artifacts, dict(id=old["id"] if old else uuid4().hex, project_id=pid,
            session_id=scope.get("session_id"), run_id=scope.get("run_id"), kind=kind, path=str(path), path_key=key,
            size_bytes=path.stat().st_size, sha256=sha.hexdigest(), created_at=old["created_at"] if old else utcnow()), ("id", "project_id", "path_key"))
    if connection is not None:
        save(connection)
    else:
        with database.transaction() as c:
            save(c)


def record_artifact(path, kind):
    if storage_backend() != "mysql":
        return
    from storage.factory import mysql_database
    from tool import workspace
    register_file(mysql_database(), workspace.WORKDIR, path, kind, scope=current_context())

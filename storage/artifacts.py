"""归档正文存入 MySQL；旧文件记录保留用于历史兼容。"""
import hashlib
from pathlib import Path
from uuid import uuid4

from storage.context import current_context
from storage.references import artifact_id, PREFIX


class SQLArtifactStore:
    def __init__(self, database, workdir):
        self.database = database
        self.workdir = Path(workdir).resolve()

    def save(self, kind, content):
        from storage.database import utcnow
        from storage.models import artifacts
        from storage.projects import ensure_project
        if kind not in ("tool_result", "transcript") or not isinstance(content, str):
            raise ValueError("归档类型或正文无效")
        scope = current_context()
        raw = content.encode("utf-8")
        identifier = uuid4().hex
        with self.database.transaction() as c:
            pid = ensure_project(c, self.workdir)
            c.execute(artifacts.insert().values(id=identifier, project_id=pid,
                session_id=scope.get("session_id"), run_id=scope.get("run_id"), kind=kind,
                path=None, path_key=None, content=content,
                line_count=content.count("\n") + int(bool(content) and not content.endswith("\n")),
                size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), created_at=utcnow()))
        return PREFIX + identifier

    def _where(self, reference):
        from sqlalchemy import select
        from storage.database import path_key
        from storage.models import artifacts, projects
        # 每次读取都以工作目录对应的项目限制查询，知道别人的编号也不能越界读取。
        pid = select(projects.c.id).where(projects.c.path_key == path_key(self.workdir)).scalar_subquery()
        return (artifacts.c.id == artifact_id(reference), artifacts.c.project_id == pid,
                artifacts.c.content.is_not(None))

    def exists(self, reference, kind):
        from sqlalchemy import select
        from storage.models import artifacts
        try:
            where = self._where(reference)
        except ValueError:
            return False
        with self.database.transaction() as c:
            return c.execute(select(artifacts.c.id).where(*where, artifacts.c.kind == kind)).first() is not None

    def read_chunk(self, reference, *, start_line=1, max_chars=80001):
        """在数据库端定位行并截取有界片段，避免分页时把整个 LONGTEXT 传回进程。"""
        from sqlalchemy import func, literal, select
        from storage.models import artifacts
        if type(start_line) is not int or start_line < 1 or type(max_chars) is not int or not 1 <= max_chars <= 80001:
            raise ValueError("归档读取范围无效")
        offset = literal(1) if start_line == 1 else func.char_length(func.substring_index(
            artifacts.c.content, "\n", start_line - 1)) + 2
        chunk = func.substring(artifacts.c.content, offset, max_chars)
        more = func.char_length(artifacts.c.content) >= offset + max_chars
        with self.database.transaction() as c:
            row = c.execute(select(chunk, more).where(*self._where(reference))).first()
        if row is None:
            raise ValueError("归档不存在、仅保存了旧文件信息，或不属于当前项目")
        return row[0], bool(row[1])

    def read_preview(self, reference, max_chars):
        return self.read_chunk(reference, max_chars=max_chars)[0]


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

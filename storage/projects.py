"""项目目录的数据库身份；界面偏好仍保存在本机。"""
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.mysql import insert

from desktop_app.catalog import validate_name
from desktop_app.store import write_json
from storage.database import iso, path_key, timestamp
from storage.models import projects
from storage.models import sessions, project_tasks


def project_view(row):
    return {key: (iso(row[key]) if key == "created_at" else row[key])
            for key in ("id", "name", "workdir", "created_at")}


def ensure_project(connection, workdir, *, project_id=None, name=None, created_at=None):
    workdir = Path(workdir).resolve()
    key = path_key(workdir)
    # 对同一路径的并发创建只保留一个 ID，已有名称和 ID 不被重置。
    statement = insert(projects).values(id=project_id or uuid4().hex, name=name or workdir.name or str(workdir),
        workdir=str(workdir), path_key=key, created_at=timestamp(created_at))
    connection.execute(statement.on_duplicate_key_update(id=projects.c.id))
    row = connection.execute(select(projects).where(projects.c.path_key == key)).mappings().one()
    if project_id and row["id"] != project_id:
        raise ValueError("该工作目录已经对应另一个数据库项目，导入未覆盖原数据。")
    return row["id"]


class SQLProjectCatalog:
    def __init__(self, database, path):
        self.database = database
        self.path = Path(path).with_name("appearance.json")
        self.warnings = []
        self.settings = {"theme": "light", "font_size": 15, "appearance_version": 2}
        source = self.path if self.path.exists() else Path(path)
        if source.exists():
            try:
                data = json.loads(source.read_text(encoding="utf-8")).get("settings", {})
                if data.get("theme") in ("light", "dark"):
                    self.settings["theme"] = data["theme"]
                if type(data.get("font_size")) is int and 12 <= data["font_size"] <= 22:
                    self.settings["font_size"] = data["font_size"]
            except (ValueError, OSError, AttributeError):
                self.warnings.append("无法读取本机外观设置，使用默认值；原文件已保留。")

    @property
    def projects(self):
        with self.database.transaction() as c:
            return [project_view(row) for row in c.execute(select(projects).where(projects.c.archived.is_(False))
                .order_by(projects.c.created_at, projects.c.id)).mappings()]

    def save(self):
        write_json(self.path, {"settings": self.settings})

    def for_directory(self, directory):
        with self.database.transaction() as c:
            project_id = ensure_project(c, directory)
            c.execute(update(projects).where(projects.c.id == project_id).values(archived=False))
            return project_view(c.execute(select(projects).where(projects.c.id == project_id)).mappings().one())

    def get(self, project_id):
        with self.database.transaction() as c:
            row = c.execute(select(projects).where(projects.c.id == project_id, projects.c.archived.is_(False))).mappings().first()
            if row is None:
                raise ValueError("项目不存在，请重新选择。")
            return project_view(row)

    def rename(self, project_id, name):
        name = validate_name(name)
        with self.database.transaction() as c:
            result = c.execute(update(projects).where(projects.c.id == project_id).values(name=name))
            if not result.rowcount:
                raise ValueError("项目不存在，请重新选择。")
        return self.get(project_id)

    def delete(self, project_id):
        with self.database.transaction() as c:
            c.execute(select(projects.c.id).where(projects.c.id == project_id).with_for_update()).one()
            # 从界面移除项目和会话，保留项目级知识及源文件；重新添加同一目录可以找回。
            c.execute(update(project_tasks).where(project_tasks.c.project_id == project_id,
                project_tasks.c.status == "in_progress").values(status="pending", owner_session_id=None))
            c.execute(delete(sessions).where(sessions.c.project_id == project_id))
            c.execute(update(projects).where(projects.c.id == project_id).values(archived=True))

    deletes_sessions = True

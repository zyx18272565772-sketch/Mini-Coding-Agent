"""保存项目目录和界面偏好，项目不包含模型消息。"""

import json
from pathlib import Path
import re
from uuid import uuid4

from desktop_app.store import now, write_json


def validate_name(name):
    if (not isinstance(name, str) or any(ord(char) < 32 or ord(char) == 127 for char in name)
            or not 1 <= len(name.strip()) <= 80):
        raise ValueError("名称需为 1 到 80 个字符，不能包含换行或控制字符。")
    return name.strip()


class ProjectCatalog:
    def __init__(self, path: Path):
        self.path = path
        self.projects = []
        self.settings = {"theme": "light", "font_size": 15, "appearance_version": 2}
        self.warnings = []
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or data.get("version") != 1
                    or not isinstance(data.get("projects"), list)
                    or not isinstance(data.get("settings"), dict)
                    or data["settings"].get("theme") not in ("dark", "light")):
                raise ValueError("项目设置格式无效")
            for project in data["projects"]:
                if (not isinstance(project, dict)
                        or not isinstance(project.get("id"), str)
                        or not re.fullmatch(r"[0-9a-f]{32}", project["id"])
                        or not all(isinstance(project.get(key), str) and project[key]
                                   for key in ("name", "workdir", "created_at"))):
                    raise ValueError("项目格式无效")
            self.projects = data["projects"]
            font_size = data["settings"].get("font_size", 15)
            if type(font_size) is not int or not 12 <= font_size <= 22:
                raise ValueError("字号格式无效")
            self.settings = {**data["settings"], "font_size": font_size, "appearance_version": 2}
            if data["settings"].get("appearance_version") != 2:
                self.settings["theme"] = "light"
                self.save()
        except (ValueError, TypeError):
            backup = path.with_name(f"workspace.corrupt-{uuid4().hex}.json")
            backup.write_bytes(path.read_bytes())
            self.warnings.append(f"项目设置无法读取，原内容已备份为 {backup.name}；将从会话目录重新恢复项目。")

    def save(self):
        write_json(self.path, {"version": 1, "projects": self.projects, "settings": self.settings})

    def for_directory(self, directory: Path) -> dict:
        directory = directory.resolve()
        for project in self.projects:
            if Path(project["workdir"]).resolve() == directory:
                return project
        project = {"id": uuid4().hex, "name": directory.name or str(directory),
                   "workdir": str(directory), "created_at": now()}
        self.projects.append(project)
        self.save()
        return project

    def get(self, project_id: str) -> dict:
        for project in self.projects:
            if project["id"] == project_id:
                return project
        raise ValueError("项目不存在，请重新选择。")

    def delete(self, project_id: str):
        self.get(project_id)
        previous = self.projects
        self.projects = [project for project in previous if project["id"] != project_id]
        try:
            self.save()
        except OSError:
            self.projects = previous
            raise

    def rename(self, project_id: str, name: str) -> dict:
        name = validate_name(name)
        project = self.get(project_id)
        previous = project["name"]
        project["name"] = name
        try:
            self.save()
        except OSError:
            project["name"] = previous
            raise
        return project

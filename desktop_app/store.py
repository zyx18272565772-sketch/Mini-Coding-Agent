"""保存会话：界面时间线与模型上下文分开，完整写入后再替换旧文件。"""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_plain(value):
    """将 SDK 内容块转换为下次仍可提交给模型的普通 JSON 对象。"""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {key: to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    return value


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(to_plain(value), ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


class SessionStore:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.warnings = []

    def create(self, workdir: Path) -> dict:
        timestamp = now()
        session = {
            "version": 1, "id": uuid4().hex, "title": "新会话",
            "workdir": str(workdir.resolve()), "created_at": timestamp,
            "updated_at": timestamp, "status": "idle",
            "messages": [], "events": [],
        }
        self.save(session)
        return session

    def save(self, session: dict):
        session_id = session["id"]
        if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("会话编号无效")
        path = self.directory / f"{session_id}.json"
        write_json(path, session)

    def load_all(self) -> list[dict]:
        self.warnings = []
        sessions = []
        for path in sorted(self.directory.glob("*.json")):
            if not re.fullmatch(r"[0-9a-f]{32}", path.stem):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if (not isinstance(data, dict) or data.get("version") != 1
                        or not re.fullmatch(r"[0-9a-f]{32}", path.stem)
                        or data.get("id") != path.stem
                        or not all(isinstance(data.get(key), list) for key in ("messages", "events"))
                        or ("todos" in data and not isinstance(data["todos"], list))
                        or not all(isinstance(data.get(key), str) for key in
                                   ("title", "workdir", "created_at", "updated_at", "status"))):
                    raise ValueError("会话文件格式无效")
                sessions.append(data)
            except (OSError, ValueError, TypeError):
                self.warnings.append(f"无法读取会话文件 {path.name}，原文件已保留。")
        return sessions

    def delete(self, session_id: str):
        if not isinstance(session_id, str) or not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("会话编号无效")
        (self.directory / f"{session_id}.json").unlink(missing_ok=True)

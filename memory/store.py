"""将每条记忆保存为 Markdown，并维护索引；此模块不依赖模型客户端。"""

from contextlib import contextmanager
import os
from pathlib import Path
import re
import tempfile
import time

import yaml


MEMORY_TYPES = ("user", "feedback", "project", "reference")
TEMPORARY_MARKERS = (
    "this session", "current session", "this turn", "current turn",
    "this task", "current task", "for now", "just this time", "today only",
    "本次会话", "当前会话", "这一轮", "当前轮次", "本次任务", "当前任务", "暂时",
)


def memory_slug(name: str) -> str:
    return re.sub(r"[^\w-]+", "-", name.strip().lower()).strip("-_")[:100] or "memory"


def validate_record(record, require_scope=False) -> dict | None:
    if not isinstance(record, dict):
        return None
    keys = ("name", "type", "description", "body")
    if any(not isinstance(record.get(key), str) or not record[key].strip() for key in keys):
        return None
    result = {key: record[key].strip() for key in keys}
    if result["type"] not in MEMORY_TYPES or memory_slug(result["name"]) == "memory":
        return None
    if require_scope and record.get("scope") not in ("persistent", "current_task"):
        return None
    if "scope" in record:
        result["scope"] = record["scope"]
    return result


def normalized(text: str) -> str:
    return " ".join(text.lower().split())


def should_store_memory(candidate, existing: list[dict]) -> bool:
    record = validate_record(candidate, require_scope=True)
    if record is None or record["scope"] != "persistent":
        return False
    text = normalized(" ".join(record[key] for key in ("name", "description", "body")))
    if any(marker in text for marker in TEMPORARY_MARKERS):
        return False
    return not any(
        memory_slug(record["name"]) == memory_slug(old["name"])
        or normalized(record["description"]) == normalized(old["description"])
        or normalized(record["body"]) == normalized(old["body"])
        for old in existing
    )


def memory_document(record: dict) -> str:
    metadata = {key: record[key] for key in ("name", "description", "type")}
    header = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{header}\n---\n\n{record['body'].strip()}\n"


def records_from_snapshot(snapshot: dict[str, str]) -> list[dict]:
    records = []
    for filename, content in sorted(snapshot.items()):
        try:
            if not content.startswith("---\n"):
                raise ValueError("缺少 YAML 文件头")
            header, body = content[4:].split("\n---\n", 1)
            metadata = yaml.safe_load(header)
            record = validate_record({**metadata, "body": body.strip()}) if isinstance(metadata, dict) else None
            if record is None:
                raise ValueError("记忆字段无效")
        except (ValueError, yaml.YAMLError) as error:
            raise ValueError(f"记忆文件 {filename} 格式无效，原文件已保留") from error
        records.append({**record, "filename": filename})
    return records


class FileMemoryStore:
    """同一工作目录共享记忆；通过文件锁与快照检查保护并发会话。"""

    def __init__(self, workdir: Path):
        self.workdir = Path(workdir).resolve()
        self.directory = self.workdir / ".memory"

    def _path(self, filename: str, allow_index=False) -> Path:
        if (not filename or "/" in filename or "\\" in filename
                or Path(filename).name != filename
                or (filename.casefold() == "memory.md" and not allow_index)):
            raise ValueError("记忆文件名无效")
        root = self.directory.resolve()
        path = (self.directory / filename).resolve()
        if not root.is_relative_to(self.workdir) or not path.is_relative_to(root):
            raise ValueError("记忆文件不能位于工作目录之外")
        return path

    @contextmanager
    def _locked(self):
        lock_path = self._path(".lock")
        self.directory.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"\0")
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
                        raise TimeoutError("记忆存储正忙，本轮跳过记忆处理")
                    time.sleep(0.02)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _snapshot(self) -> dict[str, str]:
        return {path.name: self._path(path.name).read_text(encoding="utf-8")
                for path in sorted(self.directory.glob("*.md"))
                if path.name.casefold() != "memory.md"}

    def snapshot(self) -> dict[str, str]:
        if not self.directory.exists():
            return {}
        with self._locked():
            return self._snapshot()

    def list_records(self) -> list[dict]:
        return records_from_snapshot(self.snapshot())

    def read(self, filename: str) -> str | None:
        path = self._path(filename)
        if not self.directory.exists():
            return None
        with self._locked():
            return path.read_text(encoding="utf-8") if path.is_file() else None

    def _write_text(self, filename: str, content: str):
        target = self._path(filename, allow_index=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory,
                                         prefix=".memory-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(content)
            except BaseException:
                handle.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _save_snapshot(self, snapshot: dict[str, str]):
        records = records_from_snapshot(snapshot)
        for filename, content in snapshot.items():
            self._write_text(filename, content)
        for filename in self._snapshot().keys() - snapshot.keys():
            self._path(filename).unlink()
        lines = ["# 项目记忆索引", ""]
        for record in records:
            name = " ".join(record["name"].split())
            description = " ".join(record["description"].split())
            lines.append(f"- [{name}]({record['filename']}) - {description}")
        self._write_text("MEMORY.md", "\n".join(lines) + "\n")

    def _commit(self, updated: dict[str, str], previous: dict[str, str]):
        try:
            self._save_snapshot(updated)
        except Exception:
            self._save_snapshot(previous)
            raise

    def add(self, candidates: list[dict]) -> int:
        with self._locked():
            previous = self._snapshot()
            records = records_from_snapshot(previous)
            updated = dict(previous)
            stored = 0
            for candidate in candidates:
                if not should_store_memory(candidate, records):
                    continue
                record = validate_record(candidate)
                filename = f"{memory_slug(record['name'])}.md"
                if filename.casefold() in {name.casefold() for name in updated}:
                    continue
                updated[filename] = memory_document(record)
                records.append(record)
                stored += 1
            if stored:
                self._commit(updated, previous)
            return stored

    def replace(self, records: list[dict], expected: dict[str, str]) -> bool:
        """仅当模型整理期间没有其它会话写入时，才替换旧记忆。"""
        if not records or len(records) > 30:
            raise ValueError("整理结果必须包含 1 至 30 条有效记忆")
        updated = {}
        for candidate in records:
            record = validate_record(candidate)
            if record is None or not should_store_memory({**record, "scope": "persistent"}, []):
                raise ValueError("整理结果包含无效记忆")
            filename = f"{memory_slug(record['name'])}.md"
            if filename.casefold() in {name.casefold() for name in updated}:
                raise ValueError("整理结果包含重名记忆")
            updated[filename] = memory_document(record)
        with self._locked():
            previous = self._snapshot()
            if previous != expected:
                return False
            self._commit(updated, previous)
        return True

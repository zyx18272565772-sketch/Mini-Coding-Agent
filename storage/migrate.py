"""显式导入旧文件：读取源文件、验证后在一个事务中插入，已有记录不覆盖。"""
from contextlib import contextmanager
import json
from pathlib import Path

from sqlalchemy import select

from desktop_app.store import SessionStore
from memory.store import FileMemoryStore, records_from_snapshot
from task_system.store import TaskStore
from storage.artifacts import register_file
from storage.database import path_key
from storage.memories import SQLMemoryStore
from storage.models import sessions, project_tasks
from storage.projects import ensure_project
from storage.sessions import SQLSessionStore, identifier
from storage.tasks import SQLTaskStore


class BoundDatabase:
    """让多个仓储参与同一个导入事务，禁止在中途独立提交。"""
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def transaction(self):
        yield self.connection


def read_workspace(source):
    path = source / "workspace.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("projects"), list):
        raise ValueError("项目目录文件格式无效，未导入")
    for project in data["projects"]:
        identifier(project["id"])
        if not all(isinstance(project.get(k), str) and project[k] for k in ("name", "workdir", "created_at")):
            raise ValueError("项目字段无效，未导入")
    return data["projects"]


def read_tasks(workdir):
    store = TaskStore(workdir)
    tasks = [store.load(p.stem) for p in sorted(store._root().glob("task_*.json"))]
    by_id = {task.id: task for task in tasks}
    def visit(task_id, visiting, visited):
        if task_id in visiting:
            raise ValueError("源任务存在循环依赖，未导入")
        if task_id not in by_id:
            raise ValueError("源任务缺少依赖记录，未导入")
        if task_id not in visited:
            visiting.add(task_id)
            for dependency in by_id[task_id].blockedBy:
                visit(dependency, visiting, visited)
            visiting.remove(task_id)
            visited.add(task_id)
    visited = set()
    for task in tasks:
        visit(task.id, set(), visited)
    return tasks


def read_memories(workdir):
    store = FileMemoryStore(workdir)
    snapshot = {p.name: store._path(p.name).read_text(encoding="utf-8")
                for p in sorted(store.directory.glob("*.md")) if p.name.casefold() != "memory.md"}
    records_from_snapshot(snapshot)
    return snapshot


def import_files(database, source):
    source = Path(source).resolve(strict=True)
    if not source.is_dir():
        raise ValueError("源会话目录不存在")
    source_store = SessionStore(source)
    old_sessions = source_store.load_all()
    if source_store.warnings:
        raise ValueError("存在无法读取的源会话，未导入：" + "；".join(source_store.warnings))
    if any(s["status"] in ("running", "waiting", "stopping") for s in old_sessions):
        raise ValueError("源会话标记为运行中；请先正常停止并退出旧程序，再导入。")
    old_projects = read_workspace(source)
    directories = {str(Path(p["workdir"]).resolve()): p for p in old_projects}
    for s in old_sessions:
        directories.setdefault(str(Path(s["workdir"]).resolve()), {"workdir": s["workdir"]})
    # 所有源数据先验证；源文件不加写锁、不重命名、不删除。
    prepared = [(Path(p["workdir"]).resolve(), p, read_tasks(Path(p["workdir"]).resolve()),
                 read_memories(Path(p["workdir"]).resolve())) for p in directories.values()]
    report = dict(projects=len(prepared), sessions_imported=0, sessions_skipped=0, tasks_imported=0,
                  memories_imported=0, artifacts_registered=0)
    with database.transaction() as c:
        bound = BoundDatabase(c)
        target = SQLSessionStore(bound)
        for workdir, project, tasks, snapshot in prepared:
            pid = ensure_project(c, workdir, project_id=project.get("id"), name=project.get("name"), created_at=project.get("created_at"))
            for session in old_sessions:
                if Path(session["workdir"]).resolve() != workdir:
                    continue
                if c.execute(select(sessions.c.id).where(sessions.c.id == session["id"])).first():
                    report["sessions_skipped"] += 1
                    continue
                session["project_id"] = pid
                session.pop("_db_version", None)
                target.save(session)
                report["sessions_imported"] += 1
            task_store = SQLTaskStore(workdir, bound)
            with task_store._locked():
                existing = {t.id for t in task_store._list()}
                # 先插入无依赖的任务，再建边，满足外键并支持源任务任意排序。
                missing = [t for t in tasks if t.id not in existing]
                for task in missing:
                    dependencies, task.blockedBy = task.blockedBy, []
                    task_store._save(task)
                    task.blockedBy = dependencies
                for task in missing:
                    for dependency in task.blockedBy:
                        if task_store._depends_on(dependency, task.id):
                            raise ValueError("合并后的任务图存在循环，未导入")
                    task_store._save(task)
                report["tasks_imported"] += len(missing)
            memory_store = SQLMemoryStore(workdir, bound)
            with memory_store._locked():
                previous = memory_store._snapshot()
                updated = {**snapshot, **previous}
                memory_store._commit(updated, previous)
                report["memories_imported"] += len(updated) - len(previous)
            for subdir, kind in ((".transcripts", "transcript"), (".task_outputs", "tool_result")):
                root = workdir / subdir
                if root.is_symlink() or not root.resolve().is_relative_to(workdir):
                    raise ValueError("源归档目录不能指向项目外部")
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        register_file(bound, workdir, path, kind, connection=c)
                        report["artifacts_registered"] += 1
    return report

"""存储选择集中在这里；选择 MySQL 后连接失败不会退回文件。"""
from storage.config import storage_backend


def mysql_database():
    try:
        from storage.database import get_database
    except ImportError:
        raise RuntimeError("MySQL 存储依赖未安装，请安装 requirements-mysql.txt") from None
    database = get_database()
    if not getattr(database, "verified", False):
        database.verify()
        database.verified = True
    return database


def desktop_stores(directory):
    from desktop_app.catalog import ProjectCatalog
    from desktop_app.store import SessionStore
    if storage_backend() == "file":
        return SessionStore(directory), ProjectCatalog(directory / "workspace.json")
    database = mysql_database()
    from storage.projects import SQLProjectCatalog
    from storage.sessions import SQLSessionStore
    return SQLSessionStore(database), SQLProjectCatalog(database, directory / "workspace.json")


def task_store(workdir):
    if storage_backend() == "file":
        from task_system.store import TaskStore
        return TaskStore(workdir)
    database = mysql_database()
    from storage.tasks import SQLTaskStore
    return SQLTaskStore(workdir, database)


def memory_store(workdir):
    if storage_backend() == "file":
        from memory.store import FileMemoryStore
        return FileMemoryStore(workdir)
    database = mysql_database()
    from storage.memories import SQLMemoryStore
    return SQLMemoryStore(workdir, database)

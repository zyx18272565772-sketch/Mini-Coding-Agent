"""按进程创建连接池，每次操作使用短事务；错误信息不回显 SQL 和凭据。"""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading

from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError

from storage.config import database_config, mysql_error_hint


class StorageError(RuntimeError):
    pass


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def timestamp(value=None):
    if not value:
        return utcnow()
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    return datetime.fromisoformat(value).astimezone(timezone.utc).replace(tzinfo=None)


def iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def path_key(path):
    value = os.path.normcase(str(Path(path).resolve()))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Database:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def transaction(self):
        try:
            with self.engine.begin() as connection:
                yield connection
        except SQLAlchemyError as error:
            arguments = getattr(getattr(error, "orig", None), "args", ())
            code = arguments[0] if arguments and type(arguments[0]) is int else None
            raise StorageError(f"MySQL 存储操作失败：{mysql_error_hint(code)}；未切换到文件存储") from None

    def verify(self):
        from alembic.migration import MigrationContext
        with self.transaction() as connection:
            version = MigrationContext.configure(connection).get_current_revision()
            if version != "0002_artifact_content":
                raise StorageError("MySQL 数据库尚未初始化或版本不匹配，请先运行 python -m storage.manage upgrade")


_cached = {}
_lock = threading.Lock()


def get_database():
    config = database_config()
    key = (os.getpid(), config)
    with _lock:
        if key not in _cached:
            url = URL.create("mysql+pymysql", username=config.user, password=config.password,
                             host=config.host, port=config.port, database=config.database,
                             query={"charset": "utf8mb4"})
            engine = create_engine(url, pool_pre_ping=True, pool_recycle=1800, pool_size=3,
                                   max_overflow=2, pool_timeout=5, hide_parameters=True,
                                   connect_args={"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10})
            _cached[key] = Database(engine)
        return _cached[key]


def upsert(connection, table, values, keys):
    from sqlalchemy.dialects.mysql import insert
    statement = insert(table).values(values)
    statement = statement.on_duplicate_key_update(**{name: statement.inserted[name]
        for name in values if name not in keys})
    connection.execute(statement)

"""数据库配置只在本机读取，不向模型、界面或日志暴露密码。"""

from dataclasses import dataclass, field
import os
from pathlib import Path
import re


def environment():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    return os.environ


def storage_backend(values=None):
    values = environment() if values is None else values
    backend = values.get("STORAGE_BACKEND", "file").strip().lower()
    if backend not in ("file", "mysql"):
        raise ValueError("STORAGE_BACKEND 只能是 file 或 mysql")
    return backend


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)


def database_config(values=None):
    values = environment() if values is None else values
    name = values.get("MYSQL_DATABASE", "").strip()
    user = values.get("MYSQL_USER", "").strip()
    host = values.get("MYSQL_HOST", "127.0.0.1").strip()
    if (not re.fullmatch(r"[A-Za-z0-9_]{1,64}", name) or not user or not host
            or name.lower() in {"mysql", "sys", "information_schema", "performance_schema"}):
        raise ValueError("请在本地 .env 配置 MYSQL_HOST、MYSQL_DATABASE 和 MYSQL_USER；数据库名仅使用字母、数字和下划线")
    try:
        port = int(values.get("MYSQL_PORT", "3306"))
        if not 1 <= port <= 65535:
            raise ValueError()
    except ValueError:
        raise ValueError("MYSQL_PORT 必须是 1 至 65535 的整数") from None
    return DatabaseConfig(host, port, name, user, values.get("MYSQL_PASSWORD", ""))


def mysql_error_hint(code):
    return {
        1044: "账号没有访问此数据库的权限",
        1045: "MySQL 拒绝登录，请检查 MYSQL_USER 与 MYSQL_PASSWORD",
        1049: "MYSQL_DATABASE 指定的数据库尚未创建",
        1050: "目标库已有同名表，请勿覆盖已有数据",
        1146: "缺少业务表，请先初始化数据库结构",
        1205: "等待数据库锁超时，本次事务已取消",
        1213: "发生并发事务冲突，本次事务已回滚，请重试",
        2003: "无法连接 MySQL，请检查服务是否启动、地址及端口是否正确",
        2006: "MySQL 连接已断开",
        2013: "MySQL 通信中断",
    }.get(code, "请检查连接、数据库结构和权限")

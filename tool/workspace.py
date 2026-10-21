"""工具共用的工作目录与路径解析。"""

from pathlib import Path


WORKDIR = Path.cwd().resolve()


def resolve_path(path: str) -> Path:
    """解析绝对路径；工作区外访问由工具执行前的权限检查决定。"""
    return (WORKDIR / path).resolve()

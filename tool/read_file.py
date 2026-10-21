"""读取 UTF-8 文本文件。"""

from tool.workspace import resolve_path


def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = resolve_path(path).read_text(encoding="utf-8").splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"...（还有 {len(lines) - limit} 行未显示）"]
        return "\n".join(lines)
    except Exception as exc:
        return f"错误：{exc}"

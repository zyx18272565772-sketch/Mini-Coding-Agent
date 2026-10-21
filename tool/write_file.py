"""写入 UTF-8 文本文件。"""

from tool.workspace import resolve_path


def run_write(path: str, content: str) -> str:
    try:
        file_path = resolve_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"已写入 {len(content)} 个字符到文件：{path}"
    except Exception as exc:
        return f"错误：{exc}"

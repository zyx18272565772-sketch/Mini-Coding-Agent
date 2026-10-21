"""精确替换文件中的第一处匹配文本。"""

from tool.workspace import resolve_path


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = resolve_path(path)
        text = file_path.read_text(encoding="utf-8")
        if old_text not in text:
            return f"错误：在文件 {path} 中未找到待替换的文本"
        file_path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"已修改文件：{path}"
    except Exception as exc:
        return f"错误：{exc}"

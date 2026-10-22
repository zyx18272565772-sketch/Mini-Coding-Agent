"""仅修改唯一匹配的文本，批量替换必须显式指定。"""

from tool.workspace import resolve_path


def run_edit(path: str, old_text: str, new_text: str, replace_all: bool = False) -> str:
    try:
        if not isinstance(old_text, str) or not old_text:
            raise ValueError("old_text 必须是非空字符串，请提供需要修改的原文和上下文")
        if not isinstance(new_text, str) or type(replace_all) is not bool:
            raise ValueError("new_text 必须是字符串，replace_all 必须是布尔值")
        file_path = resolve_path(path)
        raw = file_path.read_bytes()
        if b"\x00" in raw:
            return "错误：检测到二进制内容，请勿使用文本编辑工具"
        text = raw.decode("utf-8")
        # read_file 展示时统一换行，编辑时转换回原文件的 CRLF，保留 BOM 和其他原始字节。
        if "\r\n" in text and "\n" not in text.replace("\r\n", ""):
            old_text = old_text.replace("\r\n", "\n").replace("\n", "\r\n")
            new_text = new_text.replace("\r\n", "\n").replace("\n", "\r\n")
        if old_text not in text:
            return f"错误：在文件 {path} 中未找到待替换的文本，请重新读取并提供原文（不含展示行号）"
        count = text.count(old_text)
        if not replace_all and text.find(old_text, text.find(old_text) + 1) != -1:
            return f"错误：在文件 {path} 中匹配到至少 {max(2, count)} 处，请增加上下文以唯一定位；仅在确需全部替换时设置 replace_all=true"
        updated = text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)
        file_path.write_bytes(updated.encode("utf-8"))
        return f"已修改文件：{path}（替换 {count if replace_all else 1} 处）"
    except Exception as exc:
        return f"错误：{exc}"

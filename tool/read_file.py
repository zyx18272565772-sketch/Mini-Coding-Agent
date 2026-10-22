"""按行分页读取 UTF-8 文本，限制实际读取的行片段和返回长度。"""

import codecs

from tool.workspace import resolve_path


DEFAULT_LINES = 200
MAX_LINES = 1000
MAX_OUTPUT_CHARS = 20_000
MAX_LINE_BYTES = MAX_OUTPUT_CHARS * 4


def run_read(path: str, limit: int | None = None, *, start_line: int = 1,
             end_line: int | None = None) -> str:
    """行号从 1 开始，end_line 包含在范围内；limit 与 end_line 二选一。"""
    try:
        for name, value in (("start_line", start_line), ("limit", limit), ("end_line", end_line)):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError(f"{name} 必须是正整数")
        if start_line is None:
            raise ValueError("start_line 必须是正整数")
        if end_line is not None and (end_line < start_line or limit is not None):
            raise ValueError("end_line 不能小于 start_line，且不能同时指定 limit")
        count = end_line - start_line + 1 if end_line is not None else (limit or DEFAULT_LINES)
        count = min(count, MAX_LINES)
        rows = []
        used = 0
        with resolve_path(path).open("rb") as file:
            # 跳过前面的行时不解码、不保留内容，超长行也分块丢弃。
            for _ in range(start_line - 1):
                raw = file.readline(MAX_LINE_BYTES)
                if not raw:
                    return "（起始行超过文件末尾）"
                while raw and not raw.endswith(b"\n"):
                    raw = file.readline(MAX_LINE_BYTES)
            for number in range(start_line, start_line + count):
                raw = file.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    break
                if b"\x00" in raw:
                    return "错误：检测到二进制内容，请勿使用文本读取工具"
                partial = len(raw) > MAX_LINE_BYTES and not raw.endswith(b"\n")
                decoder = codecs.getincrementaldecoder("utf-8-sig" if number == 1 else "utf-8")()
                text = decoder.decode(raw, final=not partial).rstrip("\r\n")
                row = f"{number} | {text}"
                if rows and used + len(row) + 1 > MAX_OUTPUT_CHARS:
                    rows.append(f"…（达到输出上限；继续读取请设置 start_line={number}）")
                    return "\n".join(rows)
                if partial or len(row) > MAX_OUTPUT_CHARS:
                    rows.append(row[:MAX_OUTPUT_CHARS])
                    rows.append(f"…（第 {number} 行过长，行内内容已截断；后续行请设置 start_line={number + 1}，不要将截断片段当成完整行）")
                    return "\n".join(rows)
                rows.append(row)
                used += len(row) + 1
            if rows and file.read(1):
                rows.append(f"…（后续内容未读取；继续读取请设置 start_line={start_line + len(rows)}）")
        return "\n".join(rows) if rows else ("（空文件）" if start_line == 1 else "（起始行超过文件末尾）")
    except Exception as exc:
        return f"错误：{exc}"

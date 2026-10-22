"""搜索 UTF-8 文件中的字面文本，返回有限的路径、行号和匹配片段。"""

import os
from pathlib import Path
import re
import stat
import time

from tool.workspace import resolve_path
from tool import workspace


MAX_FILE_BYTES = 1_000_000
MAX_SCANNED_FILES = 5000
MAX_SECONDS = 5
MAX_OUTPUT_CHARS = 20_000
SNIPPET_CHARS = 300
IGNORED_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
    ".memory", ".tasks", ".transcripts", ".task_outputs", ".agent_data", ".pytest_cache",
}


def _is_link(path: Path) -> bool:
    """也排除 Windows 目录联接，避免遍历越界或循环。"""
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _files(root: Path, deadline: float):
    if root.is_file():
        yield root
        return

    def fail(error):
        raise error

    for current, directories, names in os.walk(root, followlinks=False, onerror=fail):
        if time.monotonic() >= deadline:
            raise TimeoutError("达到搜索时间上限，搜索未完成，请缩小目录范围")
        directories[:] = sorted(name for name in directories
                                if name.casefold() not in IGNORED_DIRS
                                and not _is_link(Path(current) / name))
        for name in sorted(names):
            candidate = Path(current) / name
            if not name.casefold().startswith(".env") and not _is_link(candidate):
                yield candidate


def run_search(query: str, path: str = ".", file_glob: str = "*",
               max_results: int = 30, case_sensitive: bool = True) -> str:
    """仅做单行字面搜索；目录中的链接不会隐式跟随，外部根路径由权限钩子审批。"""
    try:
        if not isinstance(query, str) or not query or len(query) > 200 or "\n" in query or "\r" in query:
            raise ValueError("query 必须是 1～200 个字符的单行文本，不是正则表达式")
        if not isinstance(file_glob, str) or not file_glob or type(case_sensitive) is not bool:
            raise ValueError("file_glob 必须是非空字符串，case_sensitive 必须是布尔值")
        if type(max_results) is not int or not 1 <= max_results <= 200:
            raise ValueError("max_results 必须是 1～200 的整数")
        root = resolve_path(path)
        if not root.exists():
            raise ValueError(f"搜索路径不存在：{path}")
        matcher = re.compile(re.escape(query), 0 if case_sensitive else re.IGNORECASE)
        deadline = time.monotonic() + MAX_SECONDS
        rows = []
        notices = []
        scanned = skipped = used = 0
        try:
            for file_path in _files(root, deadline):
                if time.monotonic() >= deadline or scanned >= MAX_SCANNED_FILES:
                    notices.append("达到搜索时间或文件数量上限，搜索未完成，请缩小范围")
                    break
                scanned += 1
                relative = Path(file_path.name) if root.is_file() else file_path.relative_to(root)
                pattern = file_glob.replace("\\", "/")
                if not (relative.match(pattern) or (pattern.startswith("**/") and relative.match(pattern[3:]))):
                    continue
                try:
                    if file_path.stat().st_size > MAX_FILE_BYTES:
                        skipped += 1
                        continue
                    # 字节数也在实际读取时设上限，避免扫描期间增长的文件突破限制。
                    with file_path.open("rb") as file:
                        data = file.read(MAX_FILE_BYTES + 1)
                    if len(data) > MAX_FILE_BYTES or b"\x00" in data:
                        skipped += 1
                        continue
                    text = data.decode("utf-8-sig")
                except (OSError, UnicodeError):
                    skipped += 1
                    continue
                display = (file_path.relative_to(workspace.WORKDIR).as_posix()
                           if file_path.is_relative_to(workspace.WORKDIR) else file_path.as_posix())
                for number, line in enumerate(text.splitlines(), 1):
                    match = matcher.search(line)
                    if match is None:
                        continue
                    start = max(0, match.start() - 60)
                    snippet = line[start:start + SNIPPET_CHARS]
                    if start or len(line) > len(snippet):
                        snippet = f"…{snippet}…（行内片段）"
                    row = f"{display}:{number}: {snippet}"
                    if used + len(row) + 1 > MAX_OUTPUT_CHARS:
                        notices.append("达到输出字符上限，搜索未完成，请缩小范围")
                        break
                    rows.append(row)
                    used += len(row) + 1
                    if len(rows) >= max_results:
                        notices.append("达到结果数量上限，搜索未完成，可能还有匹配项，请缩小范围")
                        break
                if notices:
                    break
        except (OSError, TimeoutError) as error:
            notices.append(f"搜索未完成：{error}")
        if skipped:
            notices.append(f"跳过 {skipped} 个二进制、非 UTF-8、超过 {MAX_FILE_BYTES} 字节或无法读取的文件，可缩小范围后用 read_file 检查")
        result = rows or ["（在已检查的文本中未找到匹配项）"]
        return "\n".join([*result, *(f"…（{notice}）" for notice in notices)])
    except Exception as exc:
        return f"错误：{exc}"

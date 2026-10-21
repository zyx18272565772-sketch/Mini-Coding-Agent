"""根据 glob 模式查找工作目录中的路径。"""

import glob

from tool import workspace


def run_glob(pattern: str) -> str:
    try:
        matches = sorted({
            match for match in glob.glob(pattern, root_dir=workspace.WORKDIR, recursive=True)
            if (workspace.WORKDIR / match).resolve().is_relative_to(workspace.WORKDIR)
        })
        shown = matches[:200]
        if len(matches) > 200:
            shown.append("...（其余匹配项已省略，请缩小匹配范围）")
        return "\n".join(shown) if shown else "（未找到匹配项）"
    except Exception as exc:
        return f"错误：{exc}"

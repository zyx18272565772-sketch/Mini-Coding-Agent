"""compact 工具只登记请求，实际压缩在本批工具全部执行后进行。"""


COMPACT_REQUESTED = "已请求在本批工具执行完毕后压缩上下文。"


def run_compact() -> str:
    return COMPACT_REQUESTED

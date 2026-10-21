"""模型请求失败的分类；只允许恢复已知的临时请求错误。"""

from anthropic import APIConnectionError, APIStatusError, APITimeoutError
import httpx2


class IncompleteStreamError(RuntimeError):
    """没有收到完整响应，不能执行其中的工具调用。"""


def describe_failure(error):
    if isinstance(error, IncompleteStreamError):
        kind, message, retryable = "stream_interrupted", "模型输出中断，已保留收到的文字，本轮工具尚未执行。", True
    elif isinstance(error, (APITimeoutError, httpx2.TimeoutException)):
        kind, message, retryable = "timeout", "模型请求超时，可以稍后继续执行。", True
    elif isinstance(error, (APIConnectionError, httpx2.TransportError)):
        kind, message, retryable = "connection", "模型连接中断，请检查网络后继续执行。", True
    elif isinstance(error, APIStatusError):
        code = error.status_code
        if code == 429:
            kind, message, retryable = "rate_limit", "模型服务请求过于频繁，请稍后继续执行。", True
        elif code in (408, 409) or code >= 500:
            kind, message, retryable = "server", f"模型服务暂时不可用（HTTP {code}），可以稍后继续执行。", True
        else:
            kind, message, retryable = "configuration", f"模型服务拒绝请求（HTTP {code}），请检查 API 配置或请求参数。", False
    elif isinstance(error, (KeyError, ImportError)):
        kind, message, retryable = "configuration", "无法启动 Agent，请检查模型配置和依赖。", False
    else:
        kind, message, retryable = "execution", f"任务执行失败：{error}", False
    return {"kind": kind, "message": message, "retryable": retryable}

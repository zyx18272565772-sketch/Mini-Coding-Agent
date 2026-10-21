"""可选的运行事件接口；命令行没有设置回调时保持原来的行为。"""

from contextlib import contextmanager
from contextvars import ContextVar


_scope = ContextVar("agent_scope", default="main")
_handler = None
_approval = None
_streaming = False


def configure(handler=None, approval=None, *, streaming=False):
    global _handler, _approval, _streaming
    _handler, _approval = handler, approval
    _streaming = bool(streaming and handler is not None)


def streaming_enabled() -> bool:
    return _streaming


def emit(event: str, **data):
    if _handler is not None:
        _handler({"type": event, "scope": _scope.get(), **data})


def usage_reporter():
    def report(usage):
        emit("usage", usage=usage)
    return report


def ask_approval(tool_name: str, args: dict, reason: str):
    if _approval is None:
        return None
    return _approval(tool_name, args, reason, _scope.get())


@contextmanager
def subagent_scope():
    token = _scope.set("subagent")
    try:
        yield
    finally:
        _scope.reset(token)

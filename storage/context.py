"""仅传播身份编号，不跨进程传递数据库连接。"""

from contextlib import contextmanager
from contextvars import ContextVar


_current = ContextVar("storage_run", default={})


def current_context():
    return _current.get()


@contextmanager
def storage_scope(*, session_id=None, run_id=None):
    token = _current.set({"session_id": session_id, "run_id": run_id})
    try:
        yield
    finally:
        _current.reset(token)

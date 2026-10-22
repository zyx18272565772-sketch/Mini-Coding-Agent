"""一次用户任务共享的模型用量统计；不限制请求次数或累计 Token。"""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from uuid import uuid4


PURPOSES = ("main", "subagent", "memory", "compact")
COUNTERS = ("calls", "input_tokens", "output_tokens", "cache_read_tokens",
            "cache_creation_tokens", "unknown_calls", "pending_calls")
_current = ContextVar("model_usage_run", default=None)


class UsageRun:
    def __init__(self, previous=None, report=None, *, run_id=None):
        self.run_id = previous["run_id"] if previous else run_id or uuid4().hex
        self.by_purpose = {name: dict.fromkeys(COUNTERS, 0) for name in PURPOSES}
        if previous:
            for name in PURPOSES:
                for key in COUNTERS:
                    value = previous.get("by_purpose", {}).get(name, {}).get(key, 0)
                    if type(value) is not int or value < 0:
                        raise ValueError("已保存的用量记录无效。")
                    self.by_purpose[name][key] = value
        self.report = report
        self.status = "running"

    def snapshot(self):
        totals = {key: sum(item[key] for item in self.by_purpose.values()) for key in COUNTERS}
        totals["total_tokens"] = sum(totals[key] for key in
                                     ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"))
        return {"run_id": self.run_id, "status": self.status, **totals,
                "by_purpose": deepcopy(self.by_purpose)}

    def publish(self):
        if self.report:
            self.report(self.snapshot())

    def begin(self, purpose):
        if purpose not in PURPOSES:
            raise ValueError("未知的模型请求用途")
        self.by_purpose[purpose]["calls"] += 1
        self.by_purpose[purpose]["pending_calls"] += 1
        self.publish()

    def finish(self, purpose, usage, *, complete=True):
        bucket = self.by_purpose[purpose]
        bucket["pending_calls"] -= 1

        def value(key):
            raw = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
            return raw if type(raw) is int and raw >= 0 else None

        for source, target in (("input_tokens", "input_tokens"), ("output_tokens", "output_tokens"),
                               ("cache_read_input_tokens", "cache_read_tokens"),
                               ("cache_creation_input_tokens", "cache_creation_tokens")):
            bucket[target] += value(source) or 0
        if not complete or value("input_tokens") is None or value("output_tokens") is None:
            bucket["unknown_calls"] += 1
        self.publish()


def current_run():
    return _current.get()


@contextmanager
def run_usage(*, previous=None, report=None, run_id=None):
    existing = current_run()
    if existing is not None:
        yield existing
        return
    run = UsageRun(previous, report, run_id=run_id)
    token = _current.set(run)
    try:
        run.publish()
        yield run
        run.status = "completed"
    except BaseException as error:
        run.status = "stopped" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "error"
        raise
    finally:
        try:
            run.publish()
        finally:
            _current.reset(token)

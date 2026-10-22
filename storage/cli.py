"""命令行也保存会话、运行、原文、事件和检查点；文件模式保持原行为。"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from agent import events
from desktop_app.store import now, to_plain
from storage.config import storage_backend
from storage.context import storage_scope


class CLIHistory:
    def __init__(self, database, workdir):
        from storage.sessions import SQLSessionStore
        from tool.task_system import configure_owner
        self.store = SQLSessionStore(database)
        self.session = self.store.create(workdir)
        configure_owner(self.session["id"])

    def record(self, event):
        event = dict(event)
        kind = event["type"]
        if kind == "checkpoint":
            self.session["messages"] = deepcopy(to_plain(event["messages"]))
        else:
            if kind == "usage":
                usage = event["usage"]
                self.session["usage"] = usage
                self.session.setdefault("usage_runs", {})[usage["run_id"]] = usage
            self.session["events"].append(dict(seq=len(self.session["events"]) + 1, time=now(),
                run_id=self.session.get("run_id"), **event))
        if kind not in ("assistant_start", "assistant_delta"):
            self.session["updated_at"] = now()
            self.store.save(self.session)

    @contextmanager
    def run(self, messages, query):
        from llm.config import configured_model
        self.session.update(messages=deepcopy(to_plain(messages)), run_id=uuid4().hex, run_attempt=1, usage=None,
            status="running", active_request=query, model=configured_model(), failure=None)
        if self.session["title"] == "新会话":
            self.session["title"] = query.splitlines()[0][:36]
        events.configure(self.record)
        try:
            self.record({"type": "user", "text": query})
            with storage_scope(session_id=self.session["id"], run_id=self.session["run_id"]):
                yield self.session["run_id"]
            self.session["status"] = "completed"
        except BaseException as error:
            from llm.errors import describe_failure
            self.session["status"] = "stopped" if isinstance(error, (KeyboardInterrupt, SystemExit)) else "error"
            self.session["failure"] = describe_failure(error)
            raise
        finally:
            try:
                self.record({"type": "state", "status": self.session["status"]})
            finally:
                events.configure()


def cli_history(workdir):
    if storage_backend() == "file":
        return None
    from storage.factory import mysql_database
    return CLIHistory(mysql_database(), Path(workdir))

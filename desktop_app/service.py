"""pywebview 的中文桌面接口；每个任务使用独立进程与模型检查点。"""

from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
import multiprocessing
from pathlib import Path
import threading

from desktop_app.store import SessionStore, now
from desktop_app.catalog import ProjectCatalog, validate_name
from desktop_app.worker import run_worker, run_worker_entry, terminate_worker
from task_system.store import TaskStore
from llm.usage import COUNTERS
from llm.config import configured_model


def api_method(method):
    @wraps(method)
    def wrapped(*args, **kwargs):
        try:
            return {"ok": True, "data": method(*args, **kwargs)}
        except (ValueError, OSError, RuntimeError, EOFError) as error:
            return {"ok": False, "error": str(error)}
    return wrapped


@dataclass
class Job:
    process: object
    pipe: object
    thread: threading.Thread | None = None
    stopping: bool = False
    ready: threading.Event = field(default_factory=threading.Event)


class DesktopService:
    def __init__(self, default_workdir: Path, data_dir: Path, *, worker_target=run_worker):
        self._default_workdir = default_workdir.resolve()
        self._store = SessionStore(data_dir)
        first_launch = not (data_dir / "workspace.json").exists()
        self._catalog = ProjectCatalog(data_dir / "workspace.json")
        self._sessions = {item["id"]: item for item in self._store.load_all()}
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._context = multiprocessing.get_context("spawn")
        self._worker_target = worker_target
        self._window = None
        if first_launch:
            self._catalog.for_directory(self._default_workdir)
        for session in self._sessions.values():
            project = self._catalog.for_directory(Path(session["workdir"]))
            if session.get("project_id") != project["id"]:
                session["project_id"] = project["id"]
                self._store.save(session)
            session["pending_approval"] = None
            if session["status"] == "limited":
                session["status"] = "error"
                session.setdefault("run_workdir", session["workdir"])
                session["failure"] = {"kind": "legacy_limit", "message": "此任务曾因旧额度限制停止，现在可以继续执行。", "retryable": True}
                self._record(session, "state", status="error")
            if session["status"] in ("running", "waiting", "stopping"):
                session["status"] = "stopped"
                self._settle_usage(session)
                self._record(session, "state", status="stopped", message="上次运行已中断，可以继续发送任务。")

    def _session(self, session_id):
        if not isinstance(session_id, str) or session_id not in self._sessions:
            raise ValueError("会话不存在，请重新选择。")
        return self._sessions[session_id]

    def _save(self, session):
        session["updated_at"] = now()
        self._store.save(session)

    def _record(self, session, kind, *, persist=True, **data):
        event = {"seq": len(session["events"]) + 1, "type": kind, "time": now(), **data}
        session["events"].append(event)
        if persist:
            self._save(session)

    def _summaries(self):
        keys = ("id", "title", "project_id", "workdir", "created_at", "updated_at", "status")
        return [dict((key, item[key]) for key in keys) for item in sorted(
            self._sessions.values(), key=lambda item: item["updated_at"], reverse=True)]

    def _view(self, session):
        return deepcopy({**{key: value for key, value in session.items() if key not in ("messages", "todos", "version", "usage_runs")},
                         **self._task_view(session), **self._usage_view(session), "model": configured_model()})

    def _usage_view(self, session):
        runs = session.get("usage_runs", {})
        total = {key: sum(run.get(key, 0) for run in runs.values()) for key in (*COUNTERS, "total_tokens")}
        return {"usage": session.get("usage"), "usage_total": total}

    def _record_usage(self, session, usage):
        session.setdefault("usage_runs", {})[usage["run_id"]] = deepcopy(usage)
        session["usage"] = deepcopy(usage)

    def _settle_usage(self, session):
        usage = session.get("usage")
        if usage and usage["status"] == "running":
            usage = deepcopy(usage)
            for bucket in usage["by_purpose"].values():
                bucket["unknown_calls"] += bucket["pending_calls"]
                bucket["pending_calls"] = 0
            usage["unknown_calls"] += usage["pending_calls"]
            usage["pending_calls"] = 0
            usage["status"] = session["status"]
            self._record_usage(session, usage)
            self._record(session, "usage", usage=usage)

    def _task_view(self, session):
        # 从项目文件读取，其他会话的任务变化也能显示；不复制到会话检查点。
        try:
            return {"tasks": TaskStore(Path(session["workdir"])).snapshot(), "task_error": ""}
        except (ValueError, OSError) as error:
            return {"tasks": [], "task_error": f"无法读取项目任务：{error}"}

    @api_method
    def bootstrap(self):
        with self._lock:
            return {"sessions": self._summaries(), "default_workdir": str(self._default_workdir),
                    "projects": deepcopy(self._catalog.projects), "settings": dict(self._catalog.settings),
                    "warnings": self._store.warnings + self._catalog.warnings, "model": configured_model()}

    @api_method
    def create_project(self):
        if self._window is None:
            raise ValueError("文件夹选择器尚未就绪。")
        from webview import FileDialog
        selected = self._window.create_file_dialog(FileDialog.FOLDER, directory=str(self._default_workdir))
        if not selected:
            return None
        path = Path(selected[0]).resolve()
        if not path.is_dir():
            raise ValueError("请选择存在的文件夹。")
        with self._lock:
            return deepcopy(self._catalog.for_directory(path))

    @api_method
    def set_theme(self, theme):
        if theme not in ("dark", "light"):
            raise ValueError("主题只能选择深色或浅色。")
        with self._lock:
            self._catalog.settings["theme"] = theme
            self._catalog.save()
            return dict(self._catalog.settings)

    @api_method
    def set_font_size(self, font_size):
        if type(font_size) is not int or not 12 <= font_size <= 22:
            raise ValueError("字号必须是 12 到 22 之间的整数。")
        with self._lock:
            self._catalog.settings["font_size"] = font_size
            self._catalog.save()
            return dict(self._catalog.settings)

    @api_method
    def delete_session(self, session_id):
        with self._lock:
            self._session(session_id)
            if session_id in self._jobs:
                raise ValueError("会话正在运行，请先停止任务再删除。")
            self._store.delete(session_id)
            del self._sessions[session_id]
            return {"deleted_session_ids": [session_id]}

    @api_method
    def delete_project(self, project_id):
        with self._lock:
            self._catalog.get(project_id)
            children = [sid for sid, session in self._sessions.items() if session["project_id"] == project_id]
            if any(sid in self._jobs for sid in children):
                raise ValueError("项目中仍有运行中的会话，请先停止任务再删除。")
            for sid in children:
                self._store.delete(sid)
                del self._sessions[sid]
            self._catalog.delete(project_id)
            return {"deleted_session_ids": children, "deleted_project_id": project_id}

    @api_method
    def create_session(self, project_id=None):
        with self._lock:
            project = (self._catalog.for_directory(self._default_workdir) if project_id is None
                       else self._catalog.get(project_id))
            session = self._store.create(Path(project["workdir"]))
            session["project_id"] = project["id"]
            session["pending_approval"] = None
            self._store.save(session)
            self._sessions[session["id"]] = session
            return self._view(session)

    @api_method
    def get_session(self, session_id):
        with self._lock:
            return self._view(self._session(session_id))

    @api_method
    def rename_project(self, project_id, name):
        with self._lock:
            return deepcopy(self._catalog.rename(project_id, name))

    @api_method
    def rename_session(self, session_id, name):
        name = validate_name(name)
        with self._lock:
            session = self._session(session_id)
            previous = dict(session)
            session.update(title=name, title_custom=True)
            try:
                self._save(session)
            except OSError:
                session.clear()
                session.update(previous)
                raise
            return self._view(session)

    @api_method
    def pick_attachments(self, session_id, kind):
        if kind not in ("file", "folder"):
            raise ValueError("请选择添加文件或文件夹。")
        with self._lock:
            directory = self._session(session_id)["workdir"]
        if self._window is None:
            raise ValueError("文件选择器尚未就绪。")
        from webview import FileDialog
        selected = self._window.create_file_dialog(
            FileDialog.OPEN if kind == "file" else FileDialog.FOLDER,
            directory=directory, allow_multiple=kind == "file",
        )
        attachments, seen = [], set()
        for item in selected or ():
            path = Path(item).resolve(strict=True)
            if not (path.is_file() if kind == "file" else path.is_dir()):
                raise ValueError("所选内容类型不匹配，请重新选择。")
            if path not in seen:
                seen.add(path)
                attachments.append({"kind": kind, "name": path.name or str(path), "path": str(path)})
        return attachments

    @api_method
    def choose_workspace(self, session_id):
        with self._lock:
            session = self._session(session_id)
            if session_id in self._jobs:
                raise ValueError("任务运行中，请先停止再更改工作目录。")
            directory = session["workdir"]
        if self._window is None:
            raise ValueError("文件夹选择器尚未就绪。")
        from webview import FileDialog
        selected = self._window.create_file_dialog(FileDialog.FOLDER, directory=directory)
        with self._lock:
            if session_id in self._jobs:
                raise ValueError("任务已经启动，本次没有更改工作目录。")
            if selected:
                path = Path(selected[0]).resolve()
                if not path.is_dir():
                    raise ValueError("请选择存在的文件夹。")
                if path != Path(session["workdir"]).resolve():
                    session["failure"] = None
                session["workdir"] = str(path)
                session["project_id"] = self._catalog.for_directory(path)["id"]
                self._save(session)
            return self._view(session)

    @api_method
    def send_message(self, session_id, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("请输入任务内容。")
        with self._lock:
            session = self._session(session_id)
            if session_id in self._jobs:
                raise ValueError("这个会话仍在运行，请等待完成或先停止。")
            if not Path(session["workdir"]).is_dir():
                raise ValueError("工作目录不存在，请重新选择文件夹。")
            text = text.strip()
            if not session["messages"] and not session.get("title_custom"):
                session["title"] = text.splitlines()[0][:36]
            session["messages"].append({"role": "user", "content": text})
            session["active_request"] = text
            self._record(session, "user", text=text)
            return self._start(session)

    @api_method
    def retry_run(self, session_id):
        with self._lock:
            session = self._session(session_id)
            if (session_id in self._jobs or session["status"] != "error"
                    or not (session.get("failure") or {}).get("retryable")
                    or not session.get("usage") or not session.get("active_request")
                    or not session["messages"]):
                raise ValueError("只有因模型连接、超时或服务临时错误而失败的任务可以继续。")
            if session.get("run_workdir") != session["workdir"]:
                raise ValueError("工作目录已改变，请在原工作目录继续，或发送新任务。")
            previous = session["usage"]
            self._record(session, "notice", text="从已保存的进度继续原任务，保留累计用量。")
            return self._start(session, previous_usage=previous)

    def _start(self, session, *, previous_usage=None):
        if not Path(session["workdir"]).is_dir():
            raise ValueError("工作目录不存在，请重新选择文件夹。")
        session_id = session["id"]
        session["status"] = "running"
        session["failure"] = None
        if previous_usage is None:
            session["usage"] = None
            session["run_workdir"] = session["workdir"]
        self._record(session, "state", status="running")
        parent, child = self._context.Pipe()
        process = self._context.Process(target=run_worker_entry, args=(child, deepcopy({
            "messages": session["messages"], "session_id": session_id, "workdir": session["workdir"],
            "active_request": session["active_request"], "previous_usage": previous_usage,
        }), self._worker_target), daemon=True)
        job = Job(process, parent)
        self._jobs[session_id] = job
        try:
            process.start()
        except Exception:
            del self._jobs[session_id]
            parent.close()
            session["status"] = "error"
            session["failure"] = {"kind": "worker", "message": "后台进程启动失败，请重新打开桌面程序。", "retryable": False}
            self._record(session, "error", message=session["failure"]["message"])
            raise RuntimeError("后台进程启动失败，请重新打开桌面程序。")
        finally:
            child.close()
        job.thread = threading.Thread(target=self._watch, args=(session_id, job), daemon=True)
        job.thread.start()
        return self._view(session)

    def _watch(self, session_id, job):
        final_status = "error"
        failure = {"kind": "worker", "message": "后台进程意外退出，本次任务已结束。", "retryable": False}
        try:
            while True:
                event = job.pipe.recv()
                with self._lock:
                    if event.get("type") == "worker_ready":
                        job.ready.set()
                        if job.stopping:
                            break
                        continue
                    if job.stopping:
                        break
                    session = self._sessions[session_id]
                    kind = event.pop("type")
                    if kind == "done":
                        final_status = "completed"
                        break
                    if kind == "failure":
                        failure = {"kind": event.get("kind", "execution"), "message": event["message"],
                                   "retryable": event.get("retryable") is True}
                        break
                    if kind == "checkpoint":
                        session["messages"] = event["messages"]
                        self._save(session)
                        continue
                    if kind == "permission":
                        session["pending_approval"] = event
                        session["status"] = "waiting"
                    if kind == "usage":
                        self._record_usage(session, event["usage"])
                    # 片段先交给轮询，完整回复、检查点或结束状态再保存，避免逐字重写会话文件。
                    self._record(session, kind, persist=kind not in ("assistant_start", "assistant_delta"), **event)
        except (EOFError, OSError):
            pass
        except Exception as error:
            failure = {"kind": "storage", "message": f"无法保存任务进度：{error}", "retryable": False}
        finally:
            job.ready.set()
            job.process.join(timeout=2)
            with self._lock:
                session = self._sessions[session_id]
                session["pending_approval"] = None
                session["status"] = "stopped" if job.stopping else final_status
                self._settle_usage(session)
                session["failure"] = failure if session["status"] == "error" else None
                if session["status"] == "error":
                    self._record(session, "error", message=failure["message"])
                self._record(session, "state", status=session["status"])
                self._jobs.pop(session_id, None)
                job.pipe.close()

    @api_method
    def poll(self, session_id, after=0):
        if not isinstance(after, int) or isinstance(after, bool) or after < 0:
            raise ValueError("事件序号无效。")
        with self._lock:
            session = self._session(session_id)
            return {"sessions": self._summaries(), "status": session["status"], **self._task_view(session),
                    **self._usage_view(session),
                    "failure": deepcopy(session.get("failure")),
                    "projects": deepcopy(self._catalog.projects), "settings": dict(self._catalog.settings),
                    "cursor": len(session["events"]),
                    "pending_approval": deepcopy(session.get("pending_approval")),
                    "events": deepcopy([event for event in session["events"] if event["seq"] > after])}

    @api_method
    def resolve_permission(self, session_id, request_id, allow):
        with self._lock:
            session = self._session(session_id)
            pending = session.get("pending_approval")
            job = self._jobs.get(session_id)
            if (not job or job.stopping or not pending or pending["request_id"] != request_id):
                raise ValueError("这条权限请求已经结束，不能再次操作。")
            if not isinstance(allow, bool):
                raise ValueError("权限决定必须是允许或拒绝。")
            job.pipe.send({"request_id": request_id, "allow": allow})
            session["pending_approval"] = None
            session["status"] = "running"
            self._record(session, "permission_resolved", request_id=request_id, allow=allow)
            return self._view(session)

    @api_method
    def stop(self, session_id):
        with self._lock:
            session = self._session(session_id)
            job = self._jobs.get(session_id)
            if job is None:
                return self._view(session)
            job.stopping = True
            session["pending_approval"] = None
            session["status"] = "stopping"
            self._record(session, "state", status="stopping")
        # Windows 的虚拟环境解释器会先启动一个 Python 子进程；过早 taskkill
        # 可能只命中启动器。握手后再清理，避免子进程持有管道导致一直显示停止中。
        job.ready.wait(timeout=3)
        terminate_worker(job.process)
        return {"status": "stopping"}

    def shutdown(self):
        for session_id in list(self._jobs):
            self.stop(session_id)
        for job in list(self._jobs.values()):
            if job.thread:
                job.thread.join(timeout=4)

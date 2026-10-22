"""独立任务进程：复用 Agent，将事件和权限请求通过管道交给窗口。"""

import os
from pathlib import Path
import pickle
import signal
import subprocess
from uuid import uuid4

from desktop_app.store import to_plain
from llm.errors import describe_failure


def send_packet(connection, value):
    # 使用独立 bytes，避免 Windows 写入已断开的管道时仍持有 BytesIO 导出缓冲区。
    connection.send_bytes(pickle.dumps(to_plain(value)))


def run_worker_entry(connection, payload, target):
    """先确认工作进程已进入 Python，再允许窗口停止整棵进程树。"""
    if os.name != "nt":
        os.setsid()
    try:
        send_packet(connection, {"type": "worker_ready"})
        if payload is None:
            payload = connection.recv()
    except (EOFError, OSError):
        connection.close()
        return
    target(connection, payload)


def run_worker(connection, payload):
    disconnected = False

    def report(event):
        nonlocal disconnected
        if disconnected:
            return
        try:
            send_packet(connection, event)
        except (EOFError, OSError):
            disconnected = True
            # 接收端消失后停止执行；finally 中的用量回报不能再次写入断管道。
            raise SystemExit(0) from None

    if os.name != "nt":
        if os.getsid(0) != os.getpid():
            os.setsid()
        def terminate(signum, _frame):
            # 让 Agent 的 finally 清理各自进程组中的后台命令。
            raise SystemExit(128 + signum)
        signal.signal(signal.SIGTERM, terminate)
    try:
        if payload.get("storage_backend"):
            os.environ["STORAGE_BACKEND"] = payload["storage_backend"]
        # 先设定工具工作目录，再导入会读取目录的系统提示词。
        os.chdir(Path(__file__).resolve().parents[1])
        from tool import workspace
        workspace.WORKDIR = Path(payload["workdir"]).resolve()
        if not workspace.WORKDIR.is_dir():
            raise ValueError("工作目录不存在，请重新选择文件夹。")

        from agent.events import configure
        from tool.task_system import configure_owner
        configure_owner(payload.get("session_id", "agent"))

        def approve(tool, args, reason, scope):
            nonlocal disconnected
            request_id = uuid4().hex
            report({"type": "permission", "request_id": request_id, "scope": scope,
                    "tool": tool, "args": args, "reason": reason})
            while True:
                try:
                    answer = connection.recv()
                except (EOFError, OSError):
                    disconnected = True
                    raise SystemExit(0) from None
                if answer.get("request_id") == request_id:
                    return "allow" if answer.get("allow") is True else "deny"

        configure(report, approve, streaming=True)
        from agent.agentloop import agent_loop
        from hook import trigger_hooks
        messages = payload["messages"]
        query = payload.get("active_request", messages[-1]["content"])
        if not payload.get("previous_usage"):
            trigger_hooks("UserPromptSubmit", query)
        options = {}
        if payload.get("previous_usage") is not None:
            options = {"previous_usage": payload["previous_usage"]}
        if payload.get("run_id"):
            options["run_id"] = payload["run_id"]
        from storage.context import storage_scope
        with storage_scope(session_id=payload.get("session_id"), run_id=payload.get("run_id")):
            agent_loop(messages, query, **options)
        report({"type": "done", "status": "completed"})
    except Exception as error:
        try:
            report({"type": "failure", **describe_failure(error)})
        except SystemExit:
            pass
    finally:
        connection.close()


def terminate_worker(process):
    """只终止本应用创建的任务进程及其子进程。"""
    if not process.is_alive():
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    process.join(timeout=3)
    if process.is_alive():
        process.kill()
        process.join(timeout=3)

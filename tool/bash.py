"""bash 工具的命令执行。"""

import os
from pathlib import Path
import signal
import subprocess
import threading
import time

from tool import workspace

COMMAND_TIMEOUT = 120


def stop_shell_process(process):
    """清理本工具启动的命令及其子进程。"""
    if os.name == "nt":
        if process.poll() is None:
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, timeout=8,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=2)


def run_bash_process(command: str, *, cwd: Path, cancel: threading.Event | None = None,
                     timeout: float | None = None) -> tuple[str, int | None]:
    """同步、后台共用命令执行器；返回输出和退出码，支持超时和取消。"""
    if not isinstance(command, str) or not command.strip():
        return "错误：命令不能为空", None
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(item in command for item in dangerous):
        return "错误：已阻止危险命令", None
    if cancel is not None and cancel.is_set():
        return "错误：命令已停止", None
    timeout = COMMAND_TIMEOUT if timeout is None else timeout
    process = None
    try:
        process = subprocess.Popen(
            command, shell=True, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        deadline = time.monotonic() + timeout
        while True:
            if cancel is not None and cancel.is_set():
                return "错误：命令已停止", None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return f"错误：命令执行超时（{timeout:g} 秒）", None
            try:
                stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                output = (stdout + stderr).strip()
                return (output[:50000] if output else "（无输出）"), process.returncode
            except subprocess.TimeoutExpired:
                continue
    except OSError as exc:
        return f"错误：{exc}", None
    finally:
        if process is not None:
            try:
                stop_shell_process(process)
            finally:
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()


def format_bash_result(output: str, exit_code: int | None) -> str:
    if exit_code not in (0, None):
        return f"错误：命令退出码为 {exit_code}\n{output}"
    return output


def run_bash(command: str, run_in_background: bool = False) -> str:
    """普通调用同步执行；后台调用由工具执行流程交给后台管理器。"""
    if not isinstance(run_in_background, bool):
        raise ValueError("run_in_background 必须是布尔值")
    if run_in_background:
        raise ValueError("后台命令需要通过 Agent 的后台任务管理器启动")
    return format_bash_result(*run_bash_process(command, cwd=workspace.WORKDIR))

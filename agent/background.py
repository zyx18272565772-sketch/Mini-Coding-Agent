"""每次 Agent Loop 独立管理后台命令；线程只产出结果，不修改聊天记录。"""

from pathlib import Path
import json
import threading
from uuid import uuid4

from tool.bash import format_bash_result, run_bash_process


class BackgroundManager:
    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()
        self.tasks: dict[str, dict] = {}
        self.results: dict[str, str] = {}
        self._ready: list[str] = []
        self._threads: list[threading.Thread] = []
        self._condition = threading.Condition()
        self._cancel = threading.Event()
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def start(self, block) -> str:
        command = block.input.get("command")
        if (block.name != "bash" or block.input.get("run_in_background") is not True
                or not isinstance(command, str) or not command.strip()
                or set(block.input) - {"command", "run_in_background"}):
            raise ValueError("后台执行只支持带有效 command 和 run_in_background=true 的 bash 调用")
        task_id = f"bg_{uuid4().hex[:8]}"
        with self._condition:
            if self._closed:
                raise ValueError("后台任务管理器已经关闭")
            self.tasks[task_id] = {"block": block, "command": command, "status": "running"}
            thread = threading.Thread(target=self._run, args=(task_id, command), daemon=True)
            try:
                thread.start()
            except Exception:
                self.tasks.pop(task_id)
                raise
            self._threads.append(thread)
        return task_id

    def _run(self, task_id: str, command: str):
        exit_code = None
        try:
            output, exit_code = run_bash_process(command, cwd=self.workdir, cancel=self._cancel)
            output = format_bash_result(output, exit_code)
            status = "completed" if exit_code == 0 else "failed"
        except Exception as error:
            output, status = f"错误：{type(error).__name__}: {error}", "failed"
        with self._condition:
            self.tasks[task_id].update(status="cancelled" if self._cancel.is_set() else status,
                                       exit_code=exit_code)
            self.results[task_id] = output
            self._ready.append(task_id)
            self._condition.notify_all()

    def has_pending(self) -> bool:
        """已完成但尚未交付的结果也需要主循环继续处理。"""
        with self._condition:
            return bool(self.tasks)

    def wait(self):
        """等待完成事件，不请求模型；短超时让 Windows 的 Ctrl+C 能被及时处理。"""
        with self._condition:
            while self.tasks and not self._ready and not self._closed:
                self._condition.wait(timeout=0.2)

    def collect(self) -> list[dict]:
        with self._condition:
            completed = [{"task_id": task_id, **self.tasks.pop(task_id),
                          "output": self.results.pop(task_id)} for task_id in self._ready]
            self._ready.clear()
            return completed

    def close(self):
        with self._condition:
            self._closed = True
            self._cancel.set()
            self._condition.notify_all()
        for thread in self._threads:
            thread.join()


def inject_background_results(messages: list, background: BackgroundManager) -> int:
    """由当前 Agent 线程交付结果，避免后台线程并发修改消息或桌面管道。"""
    from agent.events import emit
    from hook import trigger_hooks

    completed = background.collect()
    if not completed:
        return 0
    notifications = []
    for item in completed:
        block = item["block"]
        # 后台启动确认不代表命令执行完成；PostToolUse 在这里接收实际结果。
        trigger_hooks("PostToolUse", block, item["output"])
        emit("background_result", call_id=block.id, name=block.name,
             **{key: item[key] for key in ("task_id", "status", "output", "exit_code")})
        print(f"[后台] {item['task_id']} {'已完成' if item['status'] == 'completed' else '执行失败'}")
        payload = {key: value for key, value in item.items() if key != "block"}
        notifications.append({"type": "text", "text":
            "<task_notification>\n" + json.dumps(payload, ensure_ascii=False) + "\n</task_notification>"})
    if messages and messages[-1]["role"] == "user":
        content = messages[-1]["content"]
        if not isinstance(content, list):
            content = [{"type": "text", "text": str(content)}]
            messages[-1]["content"] = content
        content.extend(notifications)
    else:
        messages.append({"role": "user", "content": notifications})
    return len(completed)


def wait_for_background(messages: list, background: BackgroundManager) -> bool:
    """有未交付的命令就继续本轮；没有完成结果时只等待线程事件。"""
    from agent.events import emit

    if not background.has_pending():
        return False
    if not inject_background_results(messages, background):
        text = "正在等待后台任务完成…"
        print(f"[后台] {text}")
        emit("notice", text=text)
        background.wait()
    return True

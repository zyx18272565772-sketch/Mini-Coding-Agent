"""后台命令的线程交付、工作目录、失败和取消；不访问模型。"""

from pathlib import Path
import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent.background import BackgroundManager
from tool import bash


def bash_call(call_id="call_bg", command="echo background", **extra):
    return SimpleNamespace(id=call_id, name="bash", input={
        "command": command, "run_in_background": True, **extra,
    })


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def test_start_returns_before_command_finishes_and_result_is_collected_once(self):
        started, finish = threading.Event(), threading.Event()

        def run(command, **kwargs):
            started.set()
            self.assertTrue(finish.wait(3))
            return "实际结果", 0

        with patch("agent.background.run_bash_process", side_effect=run), BackgroundManager(self.root) as manager:
            try:
                task_id = manager.start(bash_call())
                self.assertTrue(started.wait(2))
                self.assertTrue(manager.has_pending())
                self.assertEqual(manager.collect(), [])
            finally:
                finish.set()
            manager.wait()
            results = manager.collect()
            self.assertEqual([(item["task_id"], item["status"], item["output"]) for item in results],
                             [(task_id, "completed", "实际结果")])
            self.assertFalse(manager.has_pending())
            self.assertEqual(manager.collect(), [])

    def test_separate_managers_keep_results_in_their_own_workspaces(self):
        other = self.root / "other"
        other.mkdir()
        (self.root / "same.txt").write_text("parent", encoding="utf-8")
        (other / "same.txt").write_text("child", encoding="utf-8")
        command = subprocess.list2cmdline([sys.executable, "-c", "from pathlib import Path; print(Path('same.txt').read_text())"])
        with BackgroundManager(self.root) as parent, BackgroundManager(other) as child:
            parent_id = parent.start(bash_call(command=command))
            child_id = child.start(bash_call(command=command))
            parent.wait()
            child.wait()
            self.assertEqual(parent.collect()[0]["output"], "parent")
            self.assertEqual(child.collect()[0]["output"], "child")
            self.assertNotEqual(parent_id, child_id)

    def test_command_failure_and_thread_exception_are_delivered_as_failed_results(self):
        for result in [("测试失败", 2), RuntimeError("执行器故障")]:
            with self.subTest(result=result), patch("agent.background.run_bash_process") as run, BackgroundManager(self.root) as manager:
                if isinstance(result, Exception):
                    run.side_effect = result
                else:
                    run.return_value = result
                manager.start(bash_call())
                manager.wait()
                completed = manager.collect()[0]
                self.assertEqual(completed["status"], "failed")
                self.assertIn("测试失败" if isinstance(result, tuple) else "执行器故障", completed["output"])

    def test_completed_command_can_be_collected_while_another_is_still_running(self):
        finish_first = threading.Event()

        def run(command, **kwargs):
            if command == "first":
                self.assertTrue(finish_first.wait(3))
            return command, 0

        with patch("agent.background.run_bash_process", side_effect=run), BackgroundManager(self.root) as manager:
            try:
                first = manager.start(bash_call(command="first"))
                second = manager.start(bash_call(command="second"))
                manager.wait()
                self.assertEqual([item["task_id"] for item in manager.collect()], [second])
                self.assertTrue(manager.has_pending())
            finally:
                finish_first.set()
            manager.wait()
            self.assertEqual([item["task_id"] for item in manager.collect()], [first])

    def test_thread_start_failure_does_not_leave_a_task_that_would_wait_forever(self):
        with BackgroundManager(self.root) as manager, patch("threading.Thread.start", side_effect=RuntimeError("无法启动线程")):
            with self.assertRaisesRegex(RuntimeError, "无法启动线程"):
                manager.start(bash_call())
            self.assertFalse(manager.has_pending())
            manager.wait()

    def test_close_cancels_active_commands_and_does_not_leave_a_waiting_thread(self):
        started, stopped = threading.Event(), threading.Event()

        def run(command, *, cancel, **kwargs):
            started.set()
            self.assertTrue(cancel.wait(3))
            stopped.set()
            return "错误：命令已停止", None

        with patch("agent.background.run_bash_process", side_effect=run):
            manager = BackgroundManager(self.root)
            manager.start(bash_call())
            self.assertTrue(started.wait(2))
            manager.close()
            self.assertTrue(stopped.is_set())
            self.assertEqual(manager.collect()[0]["status"], "cancelled")
            with self.assertRaises(ValueError):
                manager.start(bash_call())

    def test_only_explicit_valid_bash_requests_can_start_background_threads(self):
        invalid = [SimpleNamespace(id="read", name="read_file", input={"path": "a", "run_in_background": True}),
                   bash_call(command=""), bash_call(run_in_background="true"), bash_call(unexpected=True)]
        with patch("agent.background.run_bash_process") as run, BackgroundManager(self.root) as manager:
            for block in invalid:
                with self.subTest(block=block), self.assertRaises(ValueError):
                    manager.start(block)
            run.assert_not_called()

    def test_real_timeout_and_nonzero_exit_are_reported(self):
        command = subprocess.list2cmdline([sys.executable, "-c", "import time; time.sleep(30)"])
        started = time.monotonic()
        output, code = bash.run_bash_process(command, cwd=self.root, timeout=0.15)
        self.assertLess(time.monotonic() - started, 5, "超时后必须结束命令子进程，不能等它自然退出")
        self.assertIsNone(code)
        self.assertIn("超时", output)
        output, code = bash.run_bash_process("exit 7", cwd=self.root)
        self.assertEqual(code, 7)

    def test_already_cancelled_command_is_never_started(self):
        cancel = threading.Event()
        cancel.set()
        with patch("tool.bash.subprocess.Popen") as popen:
            output, code = bash.run_bash_process("echo should-not-run", cwd=self.root, cancel=cancel)
        popen.assert_not_called()
        self.assertIsNone(code)
        self.assertIn("停止", output)

    def test_closing_manager_stops_real_command_without_waiting_for_its_timeout(self):
        command = subprocess.list2cmdline([sys.executable, "-c",
            "import os, time; from pathlib import Path; Path('pid.txt').write_text(str(os.getpid())); time.sleep(30)"])
        manager = BackgroundManager(self.root)
        self.addCleanup(manager.close)
        manager.start(bash_call(command=command))
        deadline = time.monotonic() + 3
        while not (self.root / "pid.txt").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        pid = int((self.root / "pid.txt").read_text())
        handle = None
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            handle = kernel.OpenProcess(0x100000, False, pid)
            self.assertTrue(handle)
        try:
            started = time.monotonic()
            manager.close()
            self.assertLess(time.monotonic() - started, 5)
            if handle:
                self.assertEqual(kernel.WaitForSingleObject(handle, 1000), 0)
            else:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            self.assertEqual(manager.collect()[0]["status"], "cancelled")
        finally:
            if handle:
                kernel.CloseHandle(handle)

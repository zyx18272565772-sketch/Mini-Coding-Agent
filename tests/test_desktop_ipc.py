"""桌面通信中断和会话保存失败的回归测试，不访问外部服务。"""

from contextlib import contextmanager
import multiprocessing
import os
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch
import unittest

from desktop_app.service import DesktopService, Job
from desktop_app.worker import run_worker, run_worker_entry


def disconnected_worker(connection, payload):
    """执行真实 worker，使用接收端已关闭的真实管道。"""
    from agent.events import emit
    from agent.events import configure
    from llm.usage import run_usage

    def agent(messages, query, **options):
        with run_usage(report=lambda usage: emit("usage", usage=usage)):
            emit("checkpoint", messages=messages)
        Path(payload["workdir"], "continued.txt").write_text("不应继续", encoding="utf-8")

    try:
        with patch("agent.agentloop.agent_loop", side_effect=agent), patch("hook.trigger_hooks"):
            run_worker(connection, payload)
    finally:
        configure()


class DesktopPipeTests(unittest.TestCase):
    def test_ready_notification_handles_closed_receiver(self):
        parent, child = multiprocessing.Pipe()
        parent.close()
        target = Mock()
        try:
            run_worker_entry(child, {}, target)
            self.assertFalse(target.called)
            self.assertTrue(child.closed)
        finally:
            child.close()

    def test_disconnected_worker_stops_without_repeated_reports_or_traceback(self):
        # 使用 spawn，避免测试主进程的 Agent 回调和工作目录被改变。
        with tempfile.TemporaryDirectory() as directory:
            context = multiprocessing.get_context("spawn")
            parent, child = context.Pipe()
            parent.close()
            payload = {"workdir": directory, "messages": [{"role": "user", "content": "测试"}]}
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "offline", "MODEL_ID": "offline",
                                         "GITHUB_MCP_ENABLED": "false"}):
                process = context.Process(target=disconnected_worker, args=(child, payload))
                process.start()
            child.close()
            try:
                process.join(timeout=10)
                self.assertFalse(process.is_alive(), "通信中断后不能继续执行 Agent")
                self.assertEqual(process.exitcode, 0, "已断开的管道不能再次产生未处理异常")
                self.assertFalse(Path(directory, "continued.txt").exists())
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)


class DesktopWatcherFailureTests(unittest.TestCase):
    @contextmanager
    def watcher(self, events):
        with tempfile.TemporaryDirectory() as directory, patch("desktop_app.service.configured_model", return_value="offline"):
            root = Path(directory)
            app = DesktopService(root, root / "sessions")
            sid = app.create_session()["data"]["id"]
            process = Mock()
            process.is_alive.return_value = True
            pipe = Mock()
            pipe.recv.side_effect = events
            job = Job(process, pipe)
            app._jobs[sid] = job

            def stop(owned_process):
                owned_process.is_alive.return_value = False

            with patch("desktop_app.service.terminate_worker", side_effect=stop):
                yield app, sid, job

    def test_save_error_is_reported_instead_of_unexpected_worker_exit(self):
        with self.watcher([{"type": "checkpoint", "messages": []}]) as (app, sid, job):
            original = app._save
            saves = 0

            def fail_once(session):
                nonlocal saves
                saves += 1
                if saves == 1:
                    raise PermissionError("checkpoint denied")
                original(session)

            with patch.object(app, "_save", side_effect=fail_once):
                app._watch(sid, job)
            session = app.get_session(sid)["data"]
            self.assertEqual(session["failure"]["kind"], "storage")
            self.assertIn("checkpoint denied", session["failure"]["message"])
            self.assertNotIn(sid, app._jobs)
            self.assertFalse(job.process.is_alive(), "保存失败后不能遗留仍在执行的工作进程")
            self.assertTrue(job.pipe.close.called)

    def test_persistent_save_error_still_releases_job_and_keeps_visible_failure(self):
        with self.watcher([{"type": "checkpoint", "messages": []}]) as (app, sid, job):
            with patch.object(app, "_save", side_effect=OSError("disk unavailable")):
                app._watch(sid, job)
            session = app.get_session(sid)["data"]
            self.assertEqual(session["status"], "error")
            self.assertEqual(session["failure"]["kind"], "storage")
            self.assertIn("disk unavailable", session["failure"]["message"])
            self.assertNotIn(sid, app._jobs)
            self.assertFalse(job.process.is_alive())
            self.assertTrue(job.pipe.close.called)
            self.assertEqual(session["events"][-1]["type"], "state")

    def test_receive_error_keeps_transport_reason_and_stops_worker(self):
        with self.watcher([OSError("pipe read failed")]) as (app, sid, job):
            app._watch(sid, job)
            session = app.get_session(sid)["data"]
            self.assertEqual(session["failure"]["kind"], "communication")
            self.assertIn("pipe read failed", session["failure"]["message"])
            self.assertNotIn(sid, app._jobs)
            self.assertFalse(job.process.is_alive())
            self.assertTrue(job.pipe.close.called)


if __name__ == "__main__":
    unittest.main()

"""长会话启动协议：先完成小消息握手，再在接收线程中传输历史。"""

import pickle
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from desktop_app.service import DesktopService, Job
from desktop_app.worker import run_worker_entry


class DesktopSendTests(unittest.TestCase):
    def test_entry_receives_history_after_ready_and_handles_disconnect(self):
        payload = {"messages": [{"role": "user", "content": "历史" * 40_000}]}
        connection = Mock()
        connection.recv.return_value = payload
        target = Mock()
        with patch("desktop_app.worker.os.setsid", create=True):
            run_worker_entry(connection, None, target)
        self.assertEqual(pickle.loads(connection.send_bytes.call_args.args[0]), {"type": "worker_ready"})
        target.assert_called_once()
        self.assertIs(target.call_args.args[0], connection)
        self.assertTrue(target.call_args.args[1] is payload, "必须传入接收到的历史上下文")

        target.reset_mock()
        connection.recv.side_effect = EOFError
        with patch("desktop_app.worker.os.setsid", create=True):
            run_worker_entry(connection, None, target)
        target.assert_not_called()
        connection.close.assert_called_once()

    def test_large_history_uses_pipe_after_handshake_without_blocking_service_lock(self):
        with tempfile.TemporaryDirectory() as directory, patch("desktop_app.service.configured_model", return_value="offline"):
            root = Path(directory)
            app = DesktopService(root, root / "sessions")
            sid = app.create_session()["data"]["id"]
            payload = {"messages": [{"role": "user", "content": "history" * 40_000}]}
            entered, release = threading.Event(), threading.Event()
            pipe = Mock()
            pipe.recv.side_effect = [{"type": "worker_ready"}, {"type": "done"}]

            def send(packet):
                self.assertEqual(pickle.loads(packet), payload)
                entered.set()
                release.wait(timeout=3)

            pipe.send_bytes.side_effect = send
            process = Mock()
            job = Job(process, pipe)
            app._jobs[sid] = job
            errors = []

            def watch():
                try:
                    app._watch(sid, job, payload)
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=watch)
            thread.start()
            try:
                self.assertTrue(entered.wait(timeout=1), errors)
                self.assertTrue(job.ready.is_set())
                acquired = app._lock.acquire(timeout=.2)
                try:
                    self.assertTrue(acquired, "传输长历史时不能占用整个桌面服务的锁")
                finally:
                    if acquired:
                        app._lock.release()
            finally:
                release.set()
                thread.join(timeout=4)
            self.assertEqual(errors, [])
            self.assertEqual(app.get_session(sid)["data"]["status"], "completed")
            self.assertNotIn(sid, app._jobs)


if __name__ == "__main__":
    unittest.main()

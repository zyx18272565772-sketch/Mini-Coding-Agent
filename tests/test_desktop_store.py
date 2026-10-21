"""桌面会话必须可以恢复真实模型消息，不能串会话或覆盖损坏数据。"""

from pathlib import Path
import tempfile
import unittest

from anthropic.types import TextBlock

from desktop_app.store import SessionStore


class DesktopStoreTests(unittest.TestCase):
    def test_restart_restores_context_events_and_selected_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            session = store.create(Path(directory))
            session["messages"] = [{"role": "assistant", "content": [TextBlock(type="text", text="你好")]}]
            session["events"] = [{"seq": 1, "type": "assistant", "text": "你好"}]
            store.save(session)
            loaded = SessionStore(Path(directory)).load_all()[0]
            self.assertEqual(loaded["messages"][0]["content"], [{"type": "text", "text": "你好"}])
            self.assertEqual(loaded["events"], session["events"])
            self.assertEqual(loaded["workdir"], str(Path(directory).resolve()))

    def test_sessions_have_independent_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            first, second = store.create(Path(directory)), store.create(Path(directory))
            first["messages"].append({"role": "user", "content": "只属于第一个会话"})
            store.save(first)
            store.save(second)
            loaded = {item["id"]: item for item in store.load_all()}
            self.assertEqual(loaded[second["id"]]["messages"], [])
            self.assertEqual(len(loaded[first["id"]]["messages"]), 1)

    def test_corrupt_data_is_preserved_and_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ("a" * 32 + ".json")
            path.write_text("{损坏", encoding="utf-8")
            store = SessionStore(Path(directory))
            self.assertEqual(store.load_all(), [])
            self.assertTrue(store.warnings)
            self.assertEqual(path.read_text(encoding="utf-8"), "{损坏")

    def test_session_id_cannot_escape_storage_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(Path(directory))
            session = store.create(Path(directory))
            session["id"] = "../outside"
            with self.assertRaises(ValueError):
                store.save(session)


if __name__ == "__main__":
    unittest.main()

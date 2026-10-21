"""文件引用与显示名称：不请求模型、不移动项目源文件。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from desktop_app.service import DesktopService


class DesktopComposerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = DesktopService(self.root, self.root / "sessions")
        self.addCleanup(self.app.shutdown)
        self.session = self.app.create_session()["data"]
        self.sid = self.session["id"]

    def test_rename_persists_without_changing_directory_id_or_messages(self):
        project_id = self.session["project_id"]
        self.assertTrue(self.app.rename_project(project_id, "  测试项目  ")["ok"])
        result = self.app.rename_session(self.sid, "  接口检查  ")
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["title"], "接口检查")
        restored = DesktopService(self.root, self.root / "sessions")
        self.addCleanup(restored.shutdown)
        self.assertEqual(restored.bootstrap()["data"]["projects"][0]["name"], "测试项目")
        session = restored.get_session(self.sid)["data"]
        self.assertEqual(session["title"], "接口检查")
        self.assertEqual(session["workdir"], str(self.root.resolve()))
        self.assertEqual(restored._sessions[self.sid]["messages"], [])
        self.assertTrue(self.root.is_dir())

    def test_manual_title_survives_first_message_and_failed_save_rolls_back(self):
        self.app.rename_session(self.sid, "手动名称")
        with patch.object(self.app, "_start", return_value={}) as start:
            self.assertTrue(self.app.send_message(self.sid, "第一条消息")["ok"])
        self.assertEqual(self.app.get_session(self.sid)["data"]["title"], "手动名称")
        start.assert_called_once()
        with patch.object(self.app._store, "save", side_effect=OSError("模拟保存失败")):
            self.assertFalse(self.app.rename_session(self.sid, "不能保存")["ok"])
        self.assertEqual(self.app.get_session(self.sid)["data"]["title"], "手动名称")
        with patch.object(self.app._catalog, "save", side_effect=OSError("模拟保存失败")):
            self.assertFalse(self.app.rename_project(self.session["project_id"], "不能保存")["ok"])
        self.assertEqual(self.app.bootstrap()["data"]["projects"][0]["name"], self.root.name)

    def test_invalid_names_and_unknown_ids_are_rejected(self):
        for name in ("", "   ", "a" * 81, "一\n二", "\x00", True, None, ["名称"]):
            with self.subTest(name=name):
                self.assertFalse(self.app.rename_session(self.sid, name)["ok"])
                self.assertFalse(self.app.rename_project(self.session["project_id"], name)["ok"])
        self.assertFalse(self.app.rename_session("missing", "会话")["ok"])
        self.assertFalse(self.app.rename_project("missing", "项目")["ok"])

    def test_picker_returns_references_without_reading_or_changing_workspace(self):
        file = self.root / "中文 test.txt"
        file.write_text("不会自动读取全文", encoding="utf-8")
        folder = self.root / "参考目录"
        folder.mkdir()
        picker = Mock()
        self.app._window = picker
        picker.create_file_dialog.return_value = (str(file), str(file))
        result = self.app.pick_attachments(self.sid, "file")
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"], [{"kind": "file", "name": file.name, "path": str(file.resolve())}])
        self.assertTrue(picker.create_file_dialog.call_args.kwargs["allow_multiple"])
        picker.create_file_dialog.return_value = (str(folder),)
        self.assertEqual(self.app.pick_attachments(self.sid, "folder")["data"][0]["kind"], "folder")
        self.assertEqual(self.app.get_session(self.sid)["data"]["workdir"], self.session["workdir"])
        self.assertEqual(self.app._sessions[self.sid]["messages"], [])

    def test_picker_cancel_missing_path_and_wrong_type(self):
        self.app._window = Mock()
        self.app._window.create_file_dialog.return_value = None
        self.assertEqual(self.app.pick_attachments(self.sid, "file")["data"], [])
        self.app._window.create_file_dialog.return_value = (str(self.root),)
        self.assertFalse(self.app.pick_attachments(self.sid, "file")["ok"])
        self.app._window.create_file_dialog.return_value = (str(self.root / "missing"),)
        self.assertFalse(self.app.pick_attachments(self.sid, "folder")["ok"])
        self.assertFalse(self.app.pick_attachments(self.sid, "unknown")["ok"])

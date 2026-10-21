"""外观设置、显示模型和删除记录；不删除项目文件，不请求模型。"""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from desktop_app.service import DesktopService
from tests.test_desktop_projects import Picker


class DesktopAppearanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "sessions"
        self.app = DesktopService(self.root, self.data)
        self.addCleanup(self.app.shutdown)

    def test_font_and_theme_are_saved_and_invalid_sizes_do_not_change_settings(self):
        self.assertEqual(self.app.bootstrap()["data"]["settings"]["theme"], "light")
        self.assertTrue(self.app.set_font_size(19)["ok"])
        self.app.set_theme("dark")
        restored = DesktopService(self.root, self.data)
        self.addCleanup(restored.shutdown)
        self.assertEqual(restored.bootstrap()["data"]["settings"]["font_size"], 19)
        self.assertEqual(restored.bootstrap()["data"]["settings"]["theme"], "dark")
        for value in (True, 11, 23, "18", 15.5, None):
            self.assertFalse(self.app.set_font_size(value)["ok"])
        self.assertEqual(self.app.bootstrap()["data"]["settings"]["font_size"], 19)

    def test_model_name_is_public_metadata_without_client_or_key(self):
        with patch.dict(os.environ, {"MODEL_ID": "deepseek-test", "ANTHROPIC_API_KEY": "not-public"}):
            bootstrap = self.app.bootstrap()["data"]
            self.assertEqual(bootstrap["model"], "deepseek-test")
            session = self.app.create_session()["data"]
            self.assertEqual(session["model"], "deepseek-test")
            self.assertNotIn("not-public", str(bootstrap) + str(session))

    def test_delete_session_keeps_other_session_and_restart_does_not_restore_it(self):
        first = self.app.create_session()["data"]
        second = self.app.create_session()["data"]
        result = self.app.delete_session(first["id"])
        self.assertTrue(result["ok"])
        self.assertFalse((self.data / f"{first['id']}.json").exists())
        self.assertFalse(self.app.get_session(first["id"])["ok"])
        self.assertTrue(self.app.get_session(second["id"])["ok"])
        restored = DesktopService(self.root, self.data)
        self.addCleanup(restored.shutdown)
        self.assertEqual([s["id"] for s in restored.bootstrap()["data"]["sessions"]], [second["id"]])

    def test_delete_project_deletes_its_sessions_and_preserves_source_files(self):
        project_dir = self.root / "calculator"
        project_dir.mkdir()
        source = project_dir / "calculator.py"
        source.write_text("value = 1", encoding="utf-8")
        self.app._window = Picker(project_dir)
        project = self.app.create_project()["data"]
        a = self.app.create_session(project["id"])["data"]
        b = self.app.create_session(project["id"])["data"]
        survivor = self.app.create_session()["data"]
        self.assertTrue(self.app.delete_project(project["id"])["ok"])
        self.assertEqual(source.read_text(encoding="utf-8"), "value = 1")
        self.assertFalse(self.app.get_session(a["id"])["ok"])
        self.assertFalse(self.app.get_session(b["id"])["ok"])
        restored = DesktopService(self.root, self.data)
        self.addCleanup(restored.shutdown)
        data = restored.bootstrap()["data"]
        self.assertEqual([s["id"] for s in data["sessions"]], [survivor["id"]])
        self.assertNotIn(project["id"], [p["id"] for p in data["projects"]])

    def test_deleted_default_project_stays_deleted_and_running_jobs_are_rejected(self):
        session = self.app.create_session()["data"]
        self.app._jobs[session["id"]] = object()
        try:
            self.assertFalse(self.app.delete_session(session["id"])["ok"])
            self.assertFalse(self.app.delete_project(session["project_id"])["ok"])
        finally:
            self.app._jobs.clear()
        self.assertTrue(self.app.delete_project(session["project_id"])["ok"])
        restored = DesktopService(self.root, self.data)
        self.addCleanup(restored.shutdown)
        self.assertEqual(restored.bootstrap()["data"]["projects"], [])
        self.assertEqual(restored.bootstrap()["data"]["sessions"], [])
        self.assertFalse(self.app.delete_session("../anything")["ok"])
        self.assertFalse(self.app.delete_project("unknown")["ok"])

"""项目分组、旧会话迁移及主题设置必须可以保存和恢复。"""

from pathlib import Path
import tempfile
import unittest

from desktop_app.service import DesktopService
from desktop_app.store import SessionStore


class Picker:
    def __init__(self, folder):
        self.folder = folder

    def create_file_dialog(self, *args, **kwargs):
        return (str(self.folder),) if self.folder else None


class DesktopProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "sessions"
        self.app = DesktopService(self.root, self.data)

    def tearDown(self):
        self.app.shutdown()
        self.temp.cleanup()

    def test_project_has_multiple_sessions_with_independent_history(self):
        folder = self.root / "calculator"
        folder.mkdir()
        self.app._window = Picker(folder)
        project = self.app.create_project()["data"]
        first = self.app.create_session(project["id"])["data"]
        second = self.app.create_session(project["id"])["data"]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["project_id"], project["id"])
        self.assertEqual(second["workdir"], str(folder.resolve()))
        self.assertEqual(second["events"], [])
        self.assertEqual(project["name"], "calculator")

    def test_creating_same_directory_reuses_project_and_cancel_creates_nothing(self):
        self.app._window = Picker(self.root)
        first = self.app.create_project()["data"]
        second = self.app.create_project()["data"]
        self.assertEqual(first["id"], second["id"])
        before = self.app.bootstrap()["data"]["projects"]
        self.app._window = Picker(None)
        self.assertIsNone(self.app.create_project()["data"])
        self.assertEqual(self.app.bootstrap()["data"]["projects"], before)

    def test_legacy_sessions_are_grouped_by_directory_without_losing_content(self):
        store = SessionStore(self.data)
        a = store.create(self.root)
        a["messages"] = [{"role": "user", "content": "旧会话的内容"}]
        a["events"] = [{"seq": 1, "type": "user", "text": "旧会话的内容"}]
        store.save(a)
        other = self.root / "other"
        other.mkdir()
        b = store.create(other)
        self.app = DesktopService(self.root, self.data)
        data = self.app.bootstrap()["data"]
        self.assertEqual(len(data["projects"]), 2)
        views = {item["id"]: item for item in data["sessions"]}
        self.assertNotEqual(views[a["id"]]["project_id"], views[b["id"]]["project_id"])
        self.assertEqual(self.app.get_session(a["id"])["data"]["events"], a["events"])
        self.assertEqual(store.load_all()[0]["messages"] or store.load_all()[1]["messages"], a["messages"])

    def test_theme_and_empty_project_survive_restart(self):
        folder = self.root / "new-project"
        folder.mkdir()
        self.app._window = Picker(folder)
        project = self.app.create_project()["data"]
        self.assertTrue(self.app.set_theme("light")["ok"])
        self.app.shutdown()
        self.app = DesktopService(self.root, self.data)
        data = self.app.bootstrap()["data"]
        self.assertEqual(data["settings"]["theme"], "light")
        self.assertIn(project["id"], [item["id"] for item in data["projects"]])
        self.assertFalse(self.app.set_theme("blue")["ok"])
        self.assertEqual(self.app.bootstrap()["data"]["settings"]["theme"], "light")

    def test_choose_workspace_moves_only_current_session_to_matching_project(self):
        first = self.app.create_session()["data"]
        second = self.app.create_session(first["project_id"])["data"]
        folder = self.root / "another"
        folder.mkdir()
        self.app._window = Picker(folder)
        changed = self.app.choose_workspace(first["id"])["data"]
        self.assertNotEqual(changed["project_id"], second["project_id"])
        self.assertEqual(self.app.get_session(second["id"])["data"]["workdir"], second["workdir"])

    def test_unknown_project_cannot_create_session(self):
        self.assertFalse(self.app.create_session("unknown")["ok"])

    def test_damaged_catalog_is_backed_up_and_projects_restore_from_sessions(self):
        session = self.app.create_session()["data"]
        path = self.data / "workspace.json"
        path.write_text("{损坏的项目设置", encoding="utf-8")
        self.app = DesktopService(self.root, self.data)
        data = self.app.bootstrap()["data"]
        self.assertTrue(data["warnings"])
        self.assertEqual(len(data["sessions"]), 1)
        self.assertEqual(data["sessions"][0]["id"], session["id"])
        backups = list(self.data.glob("workspace.corrupt-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{损坏的项目设置")


if __name__ == "__main__":
    unittest.main()

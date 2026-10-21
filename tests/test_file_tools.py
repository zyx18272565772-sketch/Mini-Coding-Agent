"""使用临时工作目录验证文件工具。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tool import workspace
from tool.edit_file import run_edit
from tool.glob import run_glob
from tool.read_file import run_read
from tool.registry import TOOLS, TOOL_HANDLERS
from tool.write_file import run_write


class FileToolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.workdir = self.root / "workspace"
        self.workdir.mkdir()
        self.patcher = patch.object(workspace, "WORKDIR", self.workdir)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_fourteen_tools_have_matching_definitions_and_handlers(self):
        expected = {"bash", "read_file", "write_file", "edit_file", "glob", "load_skill", "compact", "task",
                    "create_task", "update_task", "list_tasks", "get_task", "claim_task", "complete_task"}
        self.assertEqual({item["name"] for item in TOOLS}, expected)
        self.assertEqual(len(TOOLS), 14)
        self.assertEqual(set(TOOL_HANDLERS), expected)

    def test_write_creates_parent_directories_and_read_preserves_unicode(self):
        content = "你好 Agent\nsecond line\n"
        result = run_write("nested/demo.txt", content)

        self.assertNotIn("错误：", result)
        self.assertEqual((self.workdir / "nested/demo.txt").read_text(encoding="utf-8"), content)
        self.assertEqual(run_read("nested/demo.txt"), "你好 Agent\nsecond line")

    def test_read_limit_reports_remaining_lines(self):
        (self.workdir / "demo.txt").write_text("one\ntwo\nthree", encoding="utf-8")
        self.assertEqual(run_read("demo.txt", limit=1), "one\n...（还有 2 行未显示）")

    def test_read_missing_file_returns_error_text(self):
        self.assertTrue(run_read("missing.txt").startswith("错误："))

    def test_edit_replaces_only_first_occurrence(self):
        (self.workdir / "demo.txt").write_text("old old", encoding="utf-8")
        self.assertEqual(run_edit("demo.txt", "old", "new"), "已修改文件：demo.txt")
        self.assertEqual((self.workdir / "demo.txt").read_text(encoding="utf-8"), "new old")

    def test_edit_missing_text_leaves_file_unchanged(self):
        file_path = self.workdir / "demo.txt"
        file_path.write_text("original", encoding="utf-8")
        self.assertTrue(run_edit("demo.txt", "missing", "new").startswith("错误："))
        self.assertEqual(file_path.read_text(encoding="utf-8"), "original")

    def test_resolve_path_normalizes_relative_and_absolute_paths(self):
        outside = self.root / "outside.txt"
        self.assertEqual(workspace.resolve_path("nested/../demo.txt"), self.workdir / "demo.txt")
        self.assertEqual(workspace.resolve_path("../outside.txt"), outside)
        self.assertEqual(workspace.resolve_path(str(outside)), outside)

    def test_glob_finds_nested_files_in_sorted_order(self):
        (self.workdir / "nested").mkdir()
        (self.workdir / "b.py").write_text("", encoding="utf-8")
        (self.workdir / "a.py").write_text("", encoding="utf-8")
        (self.workdir / "nested/c.py").write_text("", encoding="utf-8")
        (self.workdir / "other.txt").write_text("", encoding="utf-8")

        matches = [Path(item).as_posix() for item in run_glob("**/*.py").splitlines()]
        self.assertEqual(matches, ["a.py", "b.py", "nested/c.py"])
        self.assertEqual(run_glob("**/*.missing"), "（未找到匹配项）")

    def test_glob_does_not_return_matches_outside_workspace(self):
        (self.root / "outside.txt").write_text("", encoding="utf-8")
        self.assertEqual(run_glob("../*.txt"), "（未找到匹配项）")

    def test_glob_limits_results_to_200_matches(self):
        for number in range(205):
            (self.workdir / f"file_{number:03}.txt").touch()
        lines = run_glob("*.txt").splitlines()
        self.assertEqual(len(lines), 201)
        self.assertIn("其余匹配项已省略", lines[-1])


if __name__ == "__main__":
    unittest.main()

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

    def test_fifteen_tools_have_matching_definitions_and_handlers(self):
        expected = {"bash", "read_file", "search_files", "write_file", "edit_file", "glob", "load_skill", "compact", "task",
                    "create_task", "update_task", "list_tasks", "get_task", "claim_task", "complete_task"}
        self.assertEqual({item["name"] for item in TOOLS}, expected)
        self.assertEqual(len(TOOLS), 15)
        self.assertEqual(set(TOOL_HANDLERS), expected)

    def test_write_creates_parent_directories_and_read_preserves_unicode(self):
        content = "你好 Agent\nsecond line\n"
        result = run_write("nested/demo.txt", content)

        self.assertNotIn("错误：", result)
        self.assertEqual((self.workdir / "nested/demo.txt").read_text(encoding="utf-8"), content)
        self.assertEqual(run_read("nested/demo.txt"), "1 | 你好 Agent\n2 | second line")

    def test_read_limit_reports_remaining_lines(self):
        (self.workdir / "demo.txt").write_text("one\ntwo\nthree", encoding="utf-8")
        result = run_read("demo.txt", limit=1)
        self.assertTrue(result.startswith("1 | one\n"))
        self.assertIn("start_line=2", result)

    def test_read_selects_inclusive_line_range(self):
        (self.workdir / "demo.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
        result = run_read("demo.txt", start_line=2, end_line=3)
        self.assertTrue(result.startswith("2 | two\n3 | three\n"))
        self.assertNotIn("1 | one", result)
        self.assertIn("start_line=4", result)

    def test_read_default_page_is_bounded_and_can_continue(self):
        (self.workdir / "demo.txt").write_text("x\n" * 205, encoding="utf-8")
        first = run_read("demo.txt")
        self.assertIn("200 | x", first)
        self.assertNotIn("201 | x", first)
        self.assertIn("start_line=201", first)
        self.assertEqual(run_read("demo.txt", start_line=201),
                         "201 | x\n202 | x\n203 | x\n204 | x\n205 | x")

    def test_read_rejects_invalid_ranges_and_conflicting_options(self):
        file_path = self.workdir / "demo.txt"
        file_path.write_text("one\ntwo", encoding="utf-8")
        for arguments in ({"start_line": 0}, {"limit": -1}, {"limit": True},
                          {"end_line": 0}, {"start_line": 3, "end_line": 2},
                          {"limit": 1, "end_line": 2}):
            with self.subTest(arguments=arguments):
                self.assertTrue(run_read("demo.txt", **arguments).startswith("错误："))

    def test_read_binary_file_returns_clear_error(self):
        (self.workdir / "data.bin").write_bytes(b"text\x00data")
        self.assertIn("二进制", run_read("data.bin"))

    def test_read_long_line_has_bounded_output_and_explicit_warning(self):
        (self.workdir / "long.txt").write_text("a" * 100_000 + "\ntail\n", encoding="utf-8")
        result = run_read("long.txt")
        self.assertLess(len(result), 21_000)
        self.assertIn("截断", result)
        self.assertIn("start_line=2", result)

    def test_read_does_not_decode_unrequested_tail(self):
        (self.workdir / "mixed.txt").write_bytes(b"wanted\nnext\n" + b"\xff" * 100_000)
        result = run_read("mixed.txt", end_line=1)
        self.assertTrue(result.startswith("1 | wanted"))
        self.assertIn("start_line=2", result)

    def test_read_empty_file_and_range_past_end_are_explicit(self):
        (self.workdir / "empty.txt").touch()
        self.assertIn("空文件", run_read("empty.txt"))
        (self.workdir / "demo.txt").write_text("one", encoding="utf-8")
        self.assertIn("超过文件末尾", run_read("demo.txt", start_line=10))

    def test_read_missing_file_returns_error_text(self):
        self.assertTrue(run_read("missing.txt").startswith("错误："))

    def test_edit_ambiguous_match_does_not_modify_file(self):
        (self.workdir / "demo.txt").write_text("old old", encoding="utf-8")
        result = run_edit("demo.txt", "old", "new")
        self.assertTrue(result.startswith("错误："))
        self.assertIn("2", result)
        self.assertIn("上下文", result)
        self.assertEqual((self.workdir / "demo.txt").read_text(encoding="utf-8"), "old old")

    def test_edit_unique_context_targets_only_intended_occurrence(self):
        file_path = self.workdir / "demo.txt"
        file_path.write_text("first: old\nsecond: old\n", encoding="utf-8")
        self.assertTrue(run_edit("demo.txt", "second: old", "second: new").startswith("已修改"))
        self.assertEqual(file_path.read_text(encoding="utf-8"), "first: old\nsecond: new\n")

    def test_edit_replace_all_requires_explicit_option(self):
        file_path = self.workdir / "demo.txt"
        file_path.write_text("old old", encoding="utf-8")
        result = run_edit("demo.txt", "old", "new", replace_all=True)
        self.assertIn("2", result)
        self.assertEqual(file_path.read_text(encoding="utf-8"), "new new")

    def test_edit_empty_old_text_is_rejected(self):
        file_path = self.workdir / "demo.txt"
        file_path.write_text("original", encoding="utf-8")
        self.assertTrue(run_edit("demo.txt", "", "prefix").startswith("错误："))
        self.assertEqual(file_path.read_text(encoding="utf-8"), "original")

    def test_edit_preserves_bom_and_windows_line_endings(self):
        file_path = self.workdir / "demo.txt"
        file_path.write_bytes(b"\xef\xbb\xbffirst\r\nsecond\r\nthird\r\n")
        result = run_edit("demo.txt", "first\nsecond", "first\nchanged")
        self.assertTrue(result.startswith("已修改"))
        self.assertEqual(file_path.read_bytes(), b"\xef\xbb\xbffirst\r\nchanged\r\nthird\r\n")

    def test_edit_binary_file_is_rejected(self):
        file_path = self.workdir / "data.bin"
        file_path.write_bytes(b"old\x00data")
        self.assertIn("二进制", run_edit("data.bin", "old", "new"))
        self.assertEqual(file_path.read_bytes(), b"old\x00data")

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

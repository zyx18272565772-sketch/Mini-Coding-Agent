"""验证内容搜索的定位、过滤、资源限制和权限边界。"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tool import workspace
from tool.search_files import run_search


class SearchFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.workdir = self.root / "project"
        self.workdir.mkdir()
        patcher = patch.object(workspace, "WORKDIR", self.workdir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, path, content):
        target = self.workdir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def test_search_returns_paths_line_numbers_and_only_matching_lines(self):
        self.write("app/auth.py", "unrelated\ndef login():\n    pass\n")
        self.write("tests/test_auth.py", "def test_login():\n    pass\n")
        result = run_search("login", file_glob="**/*.py")
        self.assertIn("app/auth.py:2: def login():", result)
        self.assertIn("tests/test_auth.py:1: def test_login():", result)
        self.assertNotIn("unrelated", result)
        self.assertNotIn("    pass", result)

    def test_glob_includes_root_files_and_limits_file_types(self):
        self.write("main.py", "needle")
        self.write("readme.md", "needle")
        result = run_search("needle", file_glob="**/*.py")
        self.assertIn("main.py:1:", result)
        self.assertNotIn("readme.md", result)

    def test_search_is_literal_and_case_sensitivity_is_explicit(self):
        self.write("main.py", "a.b(\naxb(\nLOGIN\n")
        self.assertIn("main.py:1: a.b(", run_search("a.b("))
        self.assertNotIn("axb", run_search("a.b("))
        self.assertIn("未找到", run_search("login"))
        self.assertIn("LOGIN", run_search("login", case_sensitive=False))

    def test_directory_and_single_file_paths_limit_scope(self):
        self.write("app/a.py", "needle")
        self.write("tests/b.py", "needle")
        self.assertNotIn("tests/b.py", run_search("needle", path="app"))
        self.assertNotIn("app/a.py", run_search("needle", path="tests/b.py"))

    def test_default_search_excludes_generated_directories_and_secrets(self):
        self.write("main.py", "needle")
        for path in (".git/config", ".venv/lib/a.py", "node_modules/a.js", "dist/a.js",
                     "nested/__pycache__/a.py", ".memory/note.md", ".env", ".env.local"):
            self.write(path, "needle")
        result = run_search("needle")
        self.assertEqual(result, "main.py:1: needle")

    def test_result_limit_is_bounded_and_reports_partial_search(self):
        self.write("main.py", "needle\n" * 10)
        result = run_search("needle", max_results=2)
        self.assertEqual(sum("main.py:" in line for line in result.splitlines()), 2)
        self.assertIn("上限", result)

    def test_binary_invalid_utf8_and_large_files_are_skipped_with_notice(self):
        self.write("main.py", "needle")
        (self.workdir / "binary.bin").write_bytes(b"needle\x00")
        (self.workdir / "invalid.txt").write_bytes(b"\xff\xfe")
        self.write("large.txt", "needle" * 300_000)
        result = run_search("needle")
        self.assertIn("main.py:1: needle", result)
        self.assertNotIn("binary.bin:", result)
        self.assertNotIn("large.txt:", result)
        self.assertIn("跳过", result)

    def test_long_matching_line_returns_a_short_snippet_around_match(self):
        self.write("main.py", "a" * 10_000 + "needle" + "b" * 10_000)
        result = run_search("needle")
        self.assertIn("needle", result)
        self.assertIn("片段", result)
        self.assertLess(len(result), 1000)

    def test_symlink_to_outside_file_is_not_searched_implicitly(self):
        outside = self.root / "outside.txt"
        outside.write_text("secret needle", encoding="utf-8")
        try:
            (self.workdir / "link.txt").symlink_to(outside)
        except OSError as error:
            self.skipTest(f"系统不允许创建符号链接：{error}")
        self.assertNotIn("secret needle", run_search("needle"))

    def test_scan_budget_reports_incomplete_search(self):
        self.write("a.py", "unrelated")
        self.write("b.py", "needle")
        with patch("tool.search_files.MAX_SCANNED_FILES", 1):
            result = run_search("needle")
        self.assertIn("上限", result)
        self.assertIn("未完成", result)

    def test_invalid_arguments_and_missing_path_are_errors(self):
        for arguments in ({"query": ""}, {"query": "a\nb"}, {"query": None},
                          {"query": "x", "max_results": 0}, {"query": "x", "max_results": True},
                          {"query": "x", "case_sensitive": "yes"}, {"query": "x", "file_glob": ""},
                          {"query": "x", "path": "missing"}):
            with self.subTest(arguments=arguments):
                self.assertTrue(run_search(**arguments).startswith("错误："))


if __name__ == "__main__":
    unittest.main()

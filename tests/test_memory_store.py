"""验证记忆的持久化、过滤、项目隔离和整理时的数据保护。"""

import os
import multiprocessing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from memory.store import FileMemoryStore


def candidate(name="chinese", body="用户希望使用中文交流。", **changes):
    return {"name": name, "type": "user", "scope": "persistent",
            "description": f"{name} 的长期偏好", "body": body, **changes}


def add_from_process(root, prefix, ready):
    ready.wait(5)
    store = FileMemoryStore(Path(root))
    store.add([candidate(f"{prefix}-{index}", f"{prefix} 的独立偏好 {index}。") for index in range(4)])


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = FileMemoryStore(self.root)

    def test_empty_reads_do_not_create_a_memory_directory(self):
        self.assertEqual(self.store.list_records(), [])
        self.assertIsNone(self.store.read("missing.md"))
        self.assertFalse((self.root / ".memory").exists())

    def test_write_rebuilds_index_and_another_instance_can_read_it(self):
        self.assertEqual(self.store.add([candidate()]), 1)
        records = FileMemoryStore(self.root).list_records()
        self.assertEqual(records[0]["body"], "用户希望使用中文交流。")
        self.assertEqual(records[0]["type"], "user")
        self.assertIn("name: chinese", self.store.read("chinese.md"))
        index = (self.root / ".memory" / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("[chinese](chinese.md)", index)
        self.assertIn("chinese 的长期偏好", index)

    def test_three_hyphens_inside_metadata_do_not_end_the_yaml_header(self):
        self.store.add([candidate(description="说明中允许使用 --- 这样的普通文字")])
        self.assertEqual(self.store.list_records()[0]["description"], "说明中允许使用 --- 这样的普通文字")

    def test_temporary_invalid_and_duplicate_candidates_are_not_stored(self):
        self.store.add([candidate()])
        invalid = [candidate(scope="current_task"), candidate(name="other"),
                   candidate(name="temporary", body="本次任务不要创建文件。"),
                   candidate(name="wrong", type="unknown"), candidate(name="empty", body=""),
                   candidate(name="number", body=123), candidate(name="MEMORY"), {}]
        self.assertEqual(self.store.add(invalid), 0)
        self.assertEqual(len(self.store.list_records()), 1)

    def test_deduplication_also_applies_within_one_batch(self):
        self.assertEqual(self.store.add([candidate(), candidate(name="same-body")]), 1)

    def test_different_projects_do_not_share_memories(self):
        self.store.add([candidate()])
        self.assertEqual(FileMemoryStore(self.root / "another-project").list_records(), [])

    def test_parallel_processes_preserve_all_records_in_one_project(self):
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        processes = [context.Process(target=add_from_process, args=(str(self.root), prefix, ready))
                     for prefix in ("first", "second")]
        try:
            for process in processes:
                process.start()
            ready.set()
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(len(self.store.list_records()), 8)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=3)

    def test_paths_cannot_escape_the_memory_directory_or_address_the_index(self):
        self.store.add([candidate()])
        for filename in ("../secret.md", "nested/file.md", "..\\secret.md", "MEMORY.md", "memory.md"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                self.store.read(filename)

    def test_replacement_removes_old_records_and_rebuilds_index(self):
        self.store.add([candidate(), candidate(name="tests", body="项目使用 unittest。", type="project")])
        snapshot = self.store.snapshot()
        self.assertTrue(self.store.replace([candidate(name="merged", body="合并后的偏好。")], snapshot))
        self.assertEqual([item["name"] for item in self.store.list_records()], ["merged"])
        self.assertFalse((self.store.directory / "chinese.md").exists())
        self.assertIn("merged.md", (self.store.directory / "MEMORY.md").read_text(encoding="utf-8"))

    def test_concurrent_changes_are_preserved_instead_of_overwritten(self):
        self.store.add([candidate()])
        snapshot = self.store.snapshot()
        FileMemoryStore(self.root).add([candidate(name="new", body="新会话补充的项目事实。")])
        self.assertFalse(self.store.replace([candidate(name="merged")], snapshot))
        self.assertEqual(len(self.store.list_records()), 2)

    def test_invalid_consolidation_keeps_original_files(self):
        self.store.add([candidate()])
        before = self.store.snapshot()
        for records in ([], [candidate(), candidate()], [candidate(type="invalid")]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                self.store.replace(records, before)
        self.assertEqual(self.store.snapshot(), before)

    def test_failed_replacement_restores_original_records(self):
        self.store.add([candidate()])
        before = self.store.snapshot()
        real_replace = os.replace

        def fail_new_record(source, target):
            if Path(target).name == "merged.md":
                raise OSError("模拟写入失败")
            return real_replace(source, target)

        with patch("memory.store.os.replace", side_effect=fail_new_record), self.assertRaises(OSError):
            self.store.replace([candidate(name="merged")], before)
        self.assertEqual(self.store.snapshot(), before)
        self.assertIn("chinese.md", (self.store.directory / "MEMORY.md").read_text(encoding="utf-8"))

    def test_index_failure_after_old_files_are_removed_restores_them(self):
        self.store.add([candidate()])
        before = self.store.snapshot()
        real_replace = os.replace
        failed = False

        def fail_index_once(source, target):
            nonlocal failed
            if Path(target).name == "MEMORY.md" and not failed:
                failed = True
                raise OSError("模拟索引写入失败")
            return real_replace(source, target)

        with patch("memory.store.os.replace", side_effect=fail_index_once), self.assertRaises(OSError):
            self.store.replace([candidate(name="merged")], before)
        self.assertEqual(self.store.snapshot(), before)
        self.assertIn("chinese.md", (self.store.directory / "MEMORY.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

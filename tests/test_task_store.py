"""任务记录、依赖门槛和跨进程领取；不调用模型。"""

import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_system.store import TaskStore


def competing_claim(workdir, task_id, owner, start, results):
    start.wait(5)
    try:
        TaskStore(Path(workdir)).claim(task_id, owner)
        results.put(owner)
    except ValueError:
        results.put(None)


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.store = TaskStore(self.root)

    def test_reading_empty_project_does_not_create_directory(self):
        self.assertEqual(self.store.list(), [])
        self.assertFalse((self.root / ".tasks").exists())

    def test_create_persists_unicode_and_runtime_id_across_instances(self):
        task = self.store.create("  实现设备查询接口  ", "按设备编号查询")
        self.assertRegex(task.id, r"^task_[0-9a-f]{8}$")
        self.assertEqual((task.subject, task.status, task.owner, task.blockedBy),
                         ("实现设备查询接口", "pending", None, []))
        restored = TaskStore(self.root).load(task.id)
        self.assertEqual(restored, task)
        self.assertEqual(json.loads((self.root / ".tasks" / f"{task.id}.json").read_text(encoding="utf-8"))["description"],
                         "按设备编号查询")

    def test_dependency_blocks_claim_until_prerequisite_completes(self):
        first = self.store.create("实现接口")
        second = self.store.create("验证接口")
        self.store.update_dependencies(second.id, [first.id, first.id])
        self.assertEqual(self.store.load(second.id).blockedBy, [first.id])
        with self.assertRaisesRegex(ValueError, "依赖"):
            self.store.claim(second.id, "session-a")
        self.store.claim(first.id, "session-a")
        completed, unblocked = self.store.complete(first.id, "session-a")
        self.assertEqual(completed.status, "completed")
        self.assertEqual([task.id for task in unblocked], [second.id])
        self.assertEqual(self.store.load(second.id).status, "pending")
        self.assertEqual(self.store.claim(second.id, "session-b").status, "in_progress")

    def test_all_prerequisites_must_complete_and_unblocked_only_reported_once(self):
        first, second, third = [self.store.create(name) for name in ("接口", "测试数据", "验证")]
        self.store.update_dependencies(third.id, [first.id, second.id])
        self.store.claim(first.id, "a")
        self.assertEqual(self.store.complete(first.id, "a")[1], [])
        self.store.claim(second.id, "a")
        self.assertEqual([task.id for task in self.store.complete(second.id, "a")[1]], [third.id])
        unrelated = self.store.create("其他工作")
        self.store.claim(unrelated.id, "a")
        self.assertEqual(self.store.complete(unrelated.id, "a")[1], [])

    def test_dependency_rejects_self_missing_and_indirect_cycle_without_partial_update(self):
        first, second, third = [self.store.create(name) for name in ("A", "B", "C")]
        self.store.update_dependencies(second.id, [first.id])
        self.store.update_dependencies(third.id, [second.id])
        for dependencies in ([first.id], [third.id], [second.id, "task_00000000"]):
            with self.subTest(dependencies=dependencies), self.assertRaises(ValueError):
                self.store.update_dependencies(first.id, dependencies)
            self.assertEqual(self.store.load(first.id).blockedBy, [])

    def test_claimed_tasks_cannot_change_dependencies_or_be_claimed_twice(self):
        task = self.store.create("任务")
        self.store.claim(task.id, "session-a")
        with self.assertRaises(ValueError):
            self.store.claim(task.id, "session-b")
        with self.assertRaises(ValueError):
            self.store.update_dependencies(task.id, [])
        with self.assertRaisesRegex(ValueError, "领取"):
            self.store.complete(task.id, "session-b")
        self.assertEqual(self.store.load(task.id).owner, "session-a")

    def test_only_in_progress_task_can_complete(self):
        task = self.store.create("任务")
        with self.assertRaises(ValueError):
            self.store.complete(task.id, "a")
        self.store.claim(task.id, "a")
        self.store.complete(task.id, "a")
        with self.assertRaises(ValueError):
            self.store.complete(task.id, "a")

    def test_missing_dependency_stays_blocked(self):
        first, second = self.store.create("A"), self.store.create("B")
        self.store.update_dependencies(second.id, [first.id])
        (self.root / ".tasks" / f"{first.id}.json").unlink()
        with self.assertRaisesRegex(ValueError, "依赖"):
            self.store.claim(second.id, "a")

    def test_rejects_invalid_inputs_and_path_traversal(self):
        for subject in (" ", None, 1):
            with self.subTest(subject=subject), self.assertRaises(ValueError):
                self.store.create(subject)
        task = self.store.create("任务")
        for task_id in ("../outside", "task_deadbeef/../../x", None):
            with self.subTest(task_id=task_id), self.assertRaises(ValueError):
                self.store.load(task_id)
        for dependencies in ("task_deadbeef", [None], [1]):
            with self.subTest(dependencies=dependencies), self.assertRaises(ValueError):
                self.store.update_dependencies(task.id, dependencies)

    def test_failed_atomic_save_keeps_previous_state(self):
        task = self.store.create("任务")
        with patch("task_system.store.os.replace", side_effect=OSError("模拟写入失败")):
            with self.assertRaises(OSError):
                self.store.claim(task.id, "a")
        self.assertEqual(self.store.load(task.id).status, "pending")

    def test_corrupt_record_is_reported_and_preserved(self):
        task = self.store.create("任务")
        path = self.root / ".tasks" / f"{task.id}.json"
        path.write_text("{损坏", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.list()
        self.assertEqual(path.read_text(encoding="utf-8"), "{损坏")

    def test_parallel_processes_cannot_claim_the_same_task(self):
        task = self.store.create("共享任务")
        context = multiprocessing.get_context("spawn")
        start, results = context.Event(), context.Queue()
        processes = [context.Process(target=competing_claim, args=(str(self.root), task.id, owner, start, results))
                     for owner in ("session-a", "session-b")]
        try:
            for process in processes:
                process.start()
            start.set()
            winners = [results.get(timeout=10) for _ in processes]
            for process in processes:
                process.join(5)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sum(owner is not None for owner in winners), 1)
            self.assertIn(self.store.load(task.id).owner, winners)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(3)
            results.close()


if __name__ == "__main__":
    unittest.main()

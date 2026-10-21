"""使用模拟模型验证选择、按需加载、提取、降级和整理。"""

import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from memory.manager import MemoryManager
from memory.store import FileMemoryStore
from tests.test_memory_store import candidate


def response(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class MemoryManagerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = FileMemoryStore(Path(directory.name))
        self.client = Mock()
        self.notices = []
        self.manager = MemoryManager(self.store, self.client, "test-model", report=self.notices.append)
        patcher = patch("sys.stdout", io.StringIO())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_no_memory_skips_selection_request(self):
        self.assertEqual(self.manager.load_memories([{"role": "user", "content": "你好"}]), "")
        self.client.messages.create.assert_not_called()

    def test_only_selected_bodies_enter_system_context(self):
        self.store.add([candidate("chinese", "偏好正文：中文。"),
                        candidate("testing", "测试正文：unittest。", type="project")])
        self.client.messages.create.return_value = response([0])
        context = self.manager.load_memories([{"role": "user", "content": "你好"}], "用什么语言交流")
        self.assertIn("偏好正文：中文。", context)
        self.assertNotIn("测试正文：unittest。", context)
        self.assertIn("当前用户请求优先", context)
        request = self.client.messages.create.call_args.kwargs
        self.assertNotIn("tools", request)
        self.assertEqual(request["model"], "test-model")
        self.assertIn("用什么语言交流", request["messages"][0]["content"])
        self.assertNotIn("偏好正文", request["messages"][0]["content"])

    def test_empty_selection_is_respected(self):
        self.store.add([candidate()])
        self.client.messages.create.return_value = response([])
        context = self.manager.load_memories([], "中文")
        self.assertNotIn("用户希望使用中文交流。", context)

    def test_invalid_json_or_request_failure_falls_back_to_keyword_matching(self):
        self.store.add([candidate("chinese", "用户希望使用中文交流。", description="中文交流偏好")])
        for result in (response("不是 JSON"), RuntimeError("连接失败")):
            with self.subTest(result=result):
                self.client.messages.create.side_effect = None
                if isinstance(result, Exception):
                    self.client.messages.create.side_effect = result
                else:
                    self.client.messages.create.return_value = result
                context = self.manager.load_memories([], "中文解释")
                self.assertIn("用户希望使用中文交流。", context)

    def test_boolean_and_out_of_range_indices_are_ignored_and_recall_is_limited(self):
        self.store.add([candidate(f"item-{i}", f"正文{i}" * 5000) for i in range(7)])
        self.client.messages.create.return_value = response([True, -1, 100, 0, 0, 1, 2, 3, 4, 5, 6])
        context = self.manager.load_memories([], "查找偏好")
        recalled = json.loads(context.split("相关记忆正文：\n", 1)[1])
        self.assertLessEqual(len(recalled), 5)
        self.assertLessEqual(sum(len(item["content"]) for item in recalled), 20000)
        self.assertEqual(recalled[0]["source"], "item-0.md")

    def test_extraction_stores_only_durable_candidates_and_excludes_tool_output(self):
        self.client.messages.create.return_value = response([
            candidate(), candidate("temporary", "本次任务不要写文件。", scope="current_task"),
            candidate("bad", type="wrong"), candidate("duplicate"),
        ])
        messages = [{"role": "user", "content": "以后请用中文交流。"},
                    {"role": "user", "content": [{"type": "tool_result", "content": "不应提取的工具全文"}]},
                    {"role": "assistant", "content": [{"type": "text", "text": "收到。"}]}]
        self.assertEqual(self.manager.extract_memories(messages, "以后请用中文交流。"), 1)
        request = self.client.messages.create.call_args.kwargs
        self.assertIn("以后请用中文交流", request["messages"][0]["content"])
        self.assertNotIn("不应提取的工具全文", request["messages"][0]["content"])
        self.assertEqual(len(FileMemoryStore(self.store.workdir).list_records()), 1)

    def test_extraction_failure_keeps_existing_memory_and_does_not_raise(self):
        self.store.add([candidate()])
        before = self.store.snapshot()
        self.client.messages.create.side_effect = RuntimeError("模拟 API 故障")
        self.assertEqual(self.manager.extract_memories([], "请记住一些信息"), 0)
        self.assertEqual(self.store.snapshot(), before)
        self.assertTrue(any("跳过" in text for text in self.notices))

    def test_consolidation_below_threshold_does_not_call_model(self):
        self.store.add([candidate()])
        self.assertEqual(self.manager.consolidate_memories(), 0)
        self.client.messages.create.assert_not_called()

    def test_consolidation_merges_records_after_validation(self):
        self.store.add([candidate(f"item-{i}", f"用户偏好{i}。") for i in range(10)])
        self.client.messages.create.return_value = response([candidate("merged", "整理后的长期偏好。")])
        self.assertEqual(self.manager.consolidate_memories(), 1)
        self.assertEqual([item["name"] for item in self.store.list_records()], ["merged"])

    def test_invalid_consolidation_preserves_every_original_record(self):
        self.store.add([candidate(f"item-{i}", f"用户偏好{i}。") for i in range(10)])
        before = self.store.snapshot()
        self.client.messages.create.return_value = response([candidate("merged"), {"name": "损坏记录"}])
        self.assertEqual(self.manager.consolidate_memories(), 0)
        self.assertEqual(self.store.snapshot(), before)

    def test_large_consolidation_is_skipped_without_a_model_request(self):
        self.store.add([candidate(f"item-{i}", f"用户偏好{i}。" * 800) for i in range(10)])
        self.assertEqual(self.manager.consolidate_memories(), 0)
        self.client.messages.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()

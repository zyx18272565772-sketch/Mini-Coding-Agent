"""Kode 风格的窗口预算及转存规则；不连接模型或数据库。"""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from context.compactor import ContextCompactor
from tests.test_context_compactor import call_message, result_message


class ContextPolicyTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"STORAGE_BACKEND": "file", "MODEL_CONTEXT_TOKENS": "200000"}))
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.client = Mock()
        self.compactor = ContextCompactor(self.client, "mock", self.root / ".transcripts",
                                         self.root / ".task_outputs/tool-results")

    def history(self, outputs):
        messages = [dict(role="user", content="阅读项目")]
        for index, output in enumerate(outputs):
            messages.extend([call_message(f"read_{index}"), result_message(f"read_{index}", output)])
        return messages

    def test_thresholds_follow_the_configured_model_window(self):
        from context.policy import ContextPolicy
        policy = ContextPolicy(200000)
        self.assertEqual((policy.effective_limit, policy.micro_threshold, policy.summary_threshold),
                         (180000, 147000, 167000))
        large = ContextPolicy(1000000)
        self.assertEqual((large.effective_limit, large.summary_threshold), (980000, 967000))

    def test_invalid_window_is_rejected_without_connecting_to_a_model(self):
        for value in ("0", "-2", "bad", "1.5"):
            with self.subTest(value=value), patch.dict(os.environ, {"MODEL_CONTEXT_TOKENS": value}):
                with self.assertRaisesRegex(ValueError, "MODEL_CONTEXT_TOKENS"):
                    ContextCompactor(self.client, "mock", self.root / ".transcripts", self.root / "outputs")
        self.client.messages.create.assert_not_called()

    def test_seventy_small_messages_are_kept_without_archiving(self):
        messages = self.history([f"文件 {n} 已读取" for n in range(35)])
        expected = json.dumps(messages, default=str, ensure_ascii=False)
        prepared = self.compactor.prepare(messages, "阅读项目")
        self.assertEqual(json.dumps(prepared, default=str, ensure_ascii=False), expected)
        self.assertEqual(list(self.root.iterdir()), [])
        self.client.messages.create.assert_not_called()

    def test_ordinary_project_reading_keeps_all_old_results(self):
        messages = self.history([str(n) * 10000 for n in range(12)])
        expected = json.dumps(messages, default=str, ensure_ascii=False)
        prepared = self.compactor.prepare(messages, "阅读项目")
        self.assertEqual(json.dumps(prepared, default=str, ensure_ascii=False), expected)
        self.assertEqual(list(self.root.iterdir()), [])
        self.client.messages.create.assert_not_called()

    def test_micro_near_limit_keeps_recent_three_and_previews_of_older_results(self):
        outputs = [str(n) * 50000 for n in range(10)]
        messages = self.history(outputs)
        prepared = self.compactor.prepare(messages, "阅读项目")
        results = [message["content"][0] for message in prepared if self.compactor.is_tool_result(message)]
        self.assertEqual([block["tool_use_id"] for block in results], [f"read_{n}" for n in range(10)])
        for n, block in enumerate(results[:7]):
            self.assertIn("<persisted-output>", block["content"])
            self.assertIn(outputs[n][:400], block["content"])
            self.assertNotIn(outputs[n][:401], block["content"])
            saved = Path(self.compactor.persisted_output_path(block["content"]))
            self.assertEqual(saved.read_text(encoding="utf-8"), outputs[n])
        self.assertEqual([block["content"] for block in results[7:]], outputs[7:])
        self.assertEqual(len(list(self.compactor.tool_results_dir.glob("*.txt"))), 7)
        self.assertEqual(self.compactor.prepare(prepared, "阅读项目"), prepared)
        self.assertEqual(len(list(self.compactor.tool_results_dir.glob("*.txt"))), 7)
        self.client.messages.create.assert_not_called()

    def test_micro_does_not_run_when_expected_savings_are_too_small(self):
        # 工具正文共约 50k Token，但保护最近三次后只能回收 12.5k。
        messages = self.history([str(n) * 50000 for n in range(4)])
        messages[0]["content"] = "背景" * 130000
        expected = json.dumps(messages, default=str, ensure_ascii=False)
        self.assertEqual(json.dumps(self.compactor.prepare(messages, "阅读项目"), default=str, ensure_ascii=False), expected)
        self.assertEqual(list(self.root.iterdir()), [])
        self.client.messages.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()

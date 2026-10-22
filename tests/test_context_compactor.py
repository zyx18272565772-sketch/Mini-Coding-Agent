"""使用临时文件和模拟模型，验证结果转存及可恢复摘要。"""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from anthropic.types import ToolUseBlock

from context.compactor import ContextCompactor


def call_message(call_id):
    return {"role": "assistant", "content": [ToolUseBlock(
        type="tool_use", id=call_id, name="read_file", input={"path": "demo.txt"},
    )]}


def result_message(call_id, content):
    return {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": call_id, "content": content,
    }]}


class ContextCompactorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.client = Mock()
        self.client.messages.create.return_value = SimpleNamespace(content=[
            SimpleNamespace(type="text", text="已完成文件修改，仍需运行测试。"),
        ])
        self.compactor = ContextCompactor(
            self.client, "test-model", self.root / ".transcripts",
            self.root / ".task_outputs" / "tool-results",
        )

    def test_small_history_needs_no_files_or_extra_model_request(self):
        messages = [{"role": "user", "content": "完成任务"}]
        self.assertEqual(self.compactor.prepare(messages, "完成任务"), messages)
        self.assertEqual(list(self.root.iterdir()), [])
        self.client.messages.create.assert_not_called()

    def test_single_oversized_result_is_persisted_and_keeps_id_and_preview(self):
        outputs = ["甲" * 100000, "乙" * 100001, "丙" * 100002, "丁" * 100003, "戊" * 400001]
        blocks = [{"type": "tool_result", "tool_use_id": f"read_{index}", "content": output}
                  for index, output in enumerate(outputs)]
        self.compactor.persist_oversized_results([{"role": "user", "content": blocks}])
        self.assertEqual([block["tool_use_id"] for block in blocks], [f"read_{i}" for i in range(5)])
        self.assertEqual([block["content"] for block in blocks[:4]], outputs[:4])
        saved = list(self.compactor.tool_results_dir.glob("*.txt"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_text(encoding="utf-8"), outputs[4])
        self.assertIn(str(saved[0]), blocks[4]["content"])
        self.assertIn("戊" * 2000, blocks[4]["content"])
        self.assertNotIn("戊" * 2001, blocks[4]["content"])

    def test_result_at_the_single_result_limit_is_not_shortened(self):
        messages = [result_message("read", "甲" * 400000)]
        self.compactor.persist_oversized_results(messages)
        self.assertEqual(messages[0]["content"][0]["content"], "甲" * 400000)
        self.assertFalse(self.compactor.tool_results_dir.exists())

    def test_saved_output_path_stays_inside_output_directory(self):
        path = self.compactor.save_output("../../outside\\call", "完整结果")
        self.assertEqual(path.parent.resolve(), self.compactor.tool_results_dir.resolve())
        self.assertEqual(path.read_text(encoding="utf-8"), "完整结果")

    def test_message_count_alone_does_not_archive_history(self):
        messages = [{"role": "user", "content": f"消息 {index}"} for index in range(70)]
        messages[2:4] = [call_message("head"), result_message("head", "开头结果")]
        messages[23:25] = [call_message("tail"), result_message("tail", "末尾结果")]
        prepared = self.compactor.prepare(messages, "继续任务")
        self.assertEqual(prepared, messages)
        self.assertFalse(self.compactor.transcript_dir.exists())
        self.client.messages.create.assert_not_called()

    def test_micro_keeps_all_results_when_far_from_context_limit(self):
        messages = [{"role": "user", "content": "检查文件"}]
        for index in range(6):
            messages.extend([call_message(f"old_{index}"), result_message(f"old_{index}", str(index) * 500)])
        messages.extend([call_message("new"), result_message("new", "最新结果" * 500)])
        self.compactor.micro_compact(messages)
        results = [message["content"][0] for message in messages if self.compactor.is_tool_result(message)]
        for index in range(6):
            self.assertEqual(results[index]["content"], str(index) * 500)
        self.assertEqual(results[-1]["content"], "最新结果" * 500)
        self.assertFalse(self.compactor.tool_results_dir.exists())

    def test_moderate_unseen_result_is_delivered_in_full_without_summarizing(self):
        output = "最新文件内容" * 2000
        messages = [call_message("new"), result_message("new", output)]
        fitted = self.compactor.prepare(messages, "阅读文件")
        self.assertEqual(fitted[-1]["content"][0]["content"], output)
        self.assertFalse(self.compactor.tool_results_dir.exists())
        self.client.messages.create.assert_not_called()

    def test_prepare_summarizes_only_at_model_window_threshold_and_keeps_pending_request(self):
        messages = [{"role": "user", "content": "历史资料" * 130000},
                    {"role": "assistant", "content": "已阅读"},
                    {"role": "user", "content": "继续补齐除零测试，保留原有接口"}]
        request = "继续补齐除零测试，保留原有接口"
        compacted = self.compactor.prepare(messages, request)
        self.assertEqual(len(compacted), 2)
        self.assertEqual(compacted[-1], messages[-1])
        self.assertIn(request, compacted[0]["content"])
        self.assertIn("已完成文件修改", compacted[0]["content"])
        self.assertEqual(len(list(self.compactor.transcript_dir.glob("*.jsonl"))), 1)
        self.client.messages.create.assert_called_once()
        self.assertEqual(self.client.messages.create.call_args.kwargs["max_tokens"], 2000)

    def test_summary_input_retains_middle_evidence_and_structured_tool_calls(self):
        messages = [call_message("evidence"), result_message("evidence", "开头" * 20000 + "中间关键事实" + "末尾" * 40000)]
        summary_input = self.compactor.summary_input(messages)
        supplied = json.loads(summary_input)
        self.assertIn("中间关键事实", supplied[-1]["content"][0]["content"])
        self.assertEqual(supplied[0]["content"][0]["type"], "tool_use")
        self.assertEqual(supplied[0]["content"][0]["id"], "evidence")

    def test_automatic_summary_preserves_latest_tool_use_and_result_pair(self):
        messages = [{"role": "user", "content": "旧背景" * 180000},
                    call_message("recent"), result_message("recent", "最新结果")]
        compacted = self.compactor.prepare(messages, "继续检查")
        self.assertEqual(compacted[-2:], messages[-2:])
        supplied = json.loads(self.client.messages.create.call_args.kwargs["messages"][0]["content"])
        self.assertEqual(len(supplied), 1)
        self.assertNotIn("recent", json.dumps(supplied))

    def test_failed_or_empty_automatic_summary_keeps_original_history(self):
        messages = [{"role": "user", "content": "旧背景" * 180000},
                    {"role": "assistant", "content": "已检查"}, {"role": "user", "content": "继续"}]
        for outcome in (RuntimeError("offline summary failure"), SimpleNamespace(content=[])):
            with self.subTest(outcome=str(outcome)):
                self.client.messages.create.side_effect = [outcome]
                self.assertEqual(self.compactor.prepare(messages, "继续"), messages)

    def test_summary_carries_task_skill_and_mcp_state(self):
        from task_system.store import TaskStore
        task = TaskStore(self.root).create("验证接口")
        skill_call = {"role": "assistant", "content": [{"type": "tool_use", "id": "skill", "name": "load_skill", "input": {"name": "code-review"}}]}
        self.compactor.tools = [{"name": "mcp__github__search_code"}]
        self.compactor.compact_history([skill_call, result_message("skill", "审查规则")], "继续审查")
        snapshot = self.client.messages.create.call_args.kwargs["messages"][-1]["content"]
        self.assertIn(task.id, snapshot)
        self.assertIn("验证接口", snapshot)
        self.assertIn("code-review", snapshot)
        self.assertIn("mcp__github__search_code", snapshot)

    def test_summary_recovers_recent_file_ranges_with_existing_read_limits(self):
        (self.root / "demo.txt").write_text("第一行\n第二行\n第三行\n", encoding="utf-8")
        messages = [call_message("file"), result_message("file", "2 | 第二行")]
        messages[0]["content"][0].input.update(start_line=2, limit=1)
        with patch("tool.workspace.WORKDIR", self.root):
            compacted = self.compactor.compact_history(messages, "继续")
        self.assertEqual(len(compacted), 2)
        self.assertIn("2 | 第二行", compacted[1]["content"])
        self.assertNotIn("1 | 第一行", compacted[1]["content"])

    def test_summary_does_not_recover_denied_or_outside_files(self):
        outside = self.root.parent / "outside-secret.txt"
        messages = [call_message("outside"), result_message("outside", "1 | secret"),
                    call_message("denied"), result_message("denied", "用户拒绝")]
        messages[0]["content"][0].input["path"] = str(outside)
        messages[-1]["content"][0]["is_error"] = True
        with patch("tool.read_file.run_read") as read:
            self.compactor.compact_history(messages, "继续")
        read.assert_not_called()

    def test_recovery_is_limited_to_five_unique_recent_files(self):
        messages = []
        for n in range(7):
            (self.root / f"source_{n}.py").write_text("x = 1\n" * 1500, encoding="utf-8")
            call = call_message(f"file_{n}")
            call["content"][0].input.update(path=f"source_{n}.py", limit=1000)
            output = self.compactor.persisted_preview(f"file_{n}", "1 | x = 1") if n == 5 else "1 | x = 1"
            messages.extend([call, result_message(f"file_{n}", output)])
        with patch("tool.workspace.WORKDIR", self.root):
            compacted = self.compactor.compact_history(messages, "继续")
        self.assertEqual(len(compacted), 6)
        for message in compacted[1:]:
            self.assertIn("1000 | x = 1", message["content"])
            self.assertNotIn("1001 | x = 1", message["content"])
        self.assertIn("source_6.py", compacted[1]["content"])
        self.assertIn("source_2.py", compacted[-1]["content"])

    def test_reactive_summary_retains_recent_pairs_and_explicit_user_request(self):
        messages = [{"role": "user", "content": f"旧消息 {index}"} for index in range(12)]
        messages[6:8] = [call_message("recent"), result_message("recent", "刚刚读取的结果")]
        compacted = self.compactor.reactive_compact(messages, "真正的用户要求")
        self.assertEqual(compacted[1:], messages[6:])
        self.assertIn("真正的用户要求", compacted[0]["content"])
        supplied = self.client.messages.create.call_args.kwargs["messages"][0]["content"]
        self.assertEqual(json.loads(supplied), messages[:6])

    def test_an_output_marker_cannot_read_an_existing_file_outside_output_directory(self):
        outside = self.root / "outside.txt"
        outside.write_text("不应读取的内容", encoding="utf-8")
        fake = f"<persisted-output>\n完整输出：{outside}\n预览：\n普通文本\n</persisted-output>"
        preview = self.compactor.persisted_preview("safe", fake)
        self.assertNotIn("不应读取的内容", preview)
        self.assertEqual((self.compactor.tool_results_dir / "safe.txt").read_text(encoding="utf-8"), fake)


if __name__ == "__main__":
    unittest.main()

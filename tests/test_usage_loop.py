"""主子任务共享用量、完整工具结果和辅助请求归类。"""

import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from llm.usage import run_usage
from tests.test_basic_loop import response, agent
from tests.memory_support import isolate_memory

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), patch("dotenv.load_dotenv"):
    from agent import subagent


class UsageLoopTests(unittest.TestCase):
    def setUp(self):
        isolate_memory(self, agent)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        workdir = patch.object(agent.workspace, "WORKDIR", self.root)
        workdir.start()
        self.addCleanup(workdir.stop)

    def test_parent_and_child_share_counts_and_tools_continue(self):
        client = Mock()
        client.messages.create.side_effect = [response([
            {"type": "tool_use", "id": "child", "name": "task", "input": {"prompt": "分析"}},
            {"type": "tool_use", "id": "write", "name": "write_file", "input": {"path": "skip.txt", "content": "不能写"}},
        ]), response([{"type": "text", "text": "子任务完成"}]), response([{"type": "text", "text": "完成"}])]
        writer = Mock(return_value="已写入")
        snapshots = []
        messages = [{"role": "user", "content": "完成任务"}]
        with patch.object(agent, "client", client), patch.object(subagent, "client", client), \
                patch.dict(agent.TOOL_HANDLERS, {"task": subagent.run_subagent, "write_file": writer}), \
                patch.object(agent, "usage_reporter", return_value=snapshots.append):
            agent.agent_loop(messages, "完成任务")
        self.assertEqual(snapshots[-1]["calls"], 3)
        self.assertEqual(snapshots[-1]["by_purpose"]["subagent"]["calls"], 1)
        self.assertEqual(client.messages.create.call_count, 3)
        writer.assert_called_once()
        self.assertEqual([r["tool_use_id"] for r in messages[-2]["content"]], ["child", "write"])

    def test_memory_requests_are_counted_without_limiting_later_requests(self):
        from memory.manager import MemoryManager
        client = Mock()
        client.messages.create.return_value = response([{"type": "text", "text": "[]"}])
        manager = MemoryManager(Mock(), client, "test-model")
        with run_usage() as run:
            self.assertEqual(manager._ask("提取", {}, 20), [])
            self.assertEqual(run.snapshot()["by_purpose"]["memory"]["calls"], 1)
            manager.select_relevant_memories([], "测试")
            self.assertEqual(run.snapshot()["by_purpose"]["memory"]["calls"], 2)

    def test_compaction_summary_counts_towards_shared_usage(self):
        from context.compactor import ContextCompactor
        from llm.generation import create_message
        client = Mock()
        client.messages.create.return_value = response([{"type": "text", "text": "摘要"}])
        compactor = ContextCompactor(client, "test-model", self.root / "transcripts", self.root / "outputs")
        with run_usage() as run:
            self.assertEqual(compactor.summarize_history([{"role": "user", "content": "任务"}]), "摘要")
            self.assertEqual(run.snapshot()["by_purpose"]["compact"]["calls"], 1)
            create_message(client)
            self.assertEqual(run.snapshot()["calls"], 2)
        self.assertEqual(client.messages.create.call_count, 2)

    def test_resume_counts_continue_and_new_user_task_has_fresh_budget(self):
        client = Mock()
        client.messages.create.return_value = response([{"type": "text", "text": "完成"}])
        with patch.object(agent, "client", client), run_usage() as old:
            old.begin("main")
            old.finish("main", {"input_tokens": 1, "output_tokens": 1})
        previous = old.snapshot()
        snapshots = []
        with patch.object(agent, "client", client), patch.object(agent, "usage_reporter", return_value=snapshots.append):
            agent.agent_loop([{"role": "user", "content": "继续"}], "继续", previous_usage=previous)
            self.assertEqual(snapshots[-1]["calls"], 2)
            self.assertEqual(snapshots[-1]["run_id"], previous["run_id"])
            agent.agent_loop([{"role": "user", "content": "新任务"}], "新任务")
            self.assertEqual(snapshots[-1]["calls"], 1)
            self.assertNotEqual(snapshots[-1]["run_id"], previous["run_id"])

"""验证真实主循环的跨会话召回，以及 Stop、压缩和失败的衔接。"""

import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from anthropic import Anthropic
import httpx2

from agent import events
from tool import workspace
from tests.test_memory_store import candidate

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), patch("dotenv.load_dotenv"):
    from agent import agentloop as agent


def text_response(text):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class MemoryLoopTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        for patcher in (patch.object(workspace, "WORKDIR", self.root), patch("sys.stdout", io.StringIO())):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(events.configure)

    def test_new_conversation_recalls_preference_without_copying_old_messages(self):
        requests = []
        notices = []
        events.configure(notices.append)

        def transport(request):
            body = json.loads(request.content)
            requests.append(body)
            if "tools" in body:
                text = "已记下你的偏好。" if len(requests) == 1 else "你希望代码注释使用中文。"
                if len(requests) > 1:
                    self.assertIn("用户希望代码注释使用中文。", body["system"])
                    self.assertEqual(body["messages"], [{"role": "user", "content": "我的注释偏好是什么？"}])
            elif "你负责选择" in body["system"]:
                text = "[0]"
            else:
                text = json.dumps([candidate(body="用户希望代码注释使用中文。")], ensure_ascii=False) if len(requests) == 2 else "[]"
            return httpx2.Response(200, json={
                "id": "msg_test", "type": "message", "role": "assistant", "model": "test-model",
                "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })

        with Anthropic(api_key="test-key", base_url="https://example.test", max_retries=0,
                       http_client=httpx2.Client(transport=httpx2.MockTransport(transport))) as client, \
                patch.object(agent, "client", client):
            first = [{"role": "user", "content": "以后代码注释请用中文。"}]
            agent.agent_loop(first, first[0]["content"])
            self.assertTrue((self.root / ".memory" / "chinese.md").is_file())
            second = [{"role": "user", "content": "我的注释偏好是什么？"}]
            agent.agent_loop(second, second[0]["content"])
        self.assertEqual(len(requests), 5)
        self.assertEqual(len(second), 2)
        self.assertTrue(any(event["type"] == "notice" and "已召回" in event["text"] for event in notices))

    def test_memory_runs_once_at_entry_and_after_stop_allows_finishing(self):
        memory = Mock()
        memory.load_memories.return_value = "\n记忆背景"
        memory.extract_memories.return_value = 1
        client = Mock()
        client.messages.create.side_effect = [text_response("第一次回答"), text_response("最终回答")]
        order = []
        memory.load_memories.side_effect = lambda *args: order.append("recall") or "\n记忆背景"
        memory.extract_memories.side_effect = lambda *args: order.append("extract") or 1
        memory.consolidate_memories.side_effect = lambda: order.append("consolidate")
        stops = iter(["还需要核对", None])

        def stop(*args):
            order.append("stop")
            return next(stops)

        with patch.object(agent, "MemoryManager", return_value=memory), patch.object(agent, "client", client), \
                patch.object(agent, "trigger_hooks", side_effect=stop):
            messages = [{"role": "user", "content": "原始请求"}]
            agent.agent_loop(messages, "原始请求")
        self.assertEqual(order, ["recall", "stop", "stop", "extract", "consolidate"])
        self.assertEqual(memory.load_memories.call_count, 1)
        self.assertTrue(all(call.kwargs["system"].endswith("记忆背景") for call in client.messages.create.call_args_list))

    def test_working_request_failure_does_not_extract_memory(self):
        memory = Mock()
        memory.load_memories.return_value = ""
        client = Mock()
        client.messages.create.side_effect = RuntimeError("工作请求失败")
        with patch.object(agent, "MemoryManager", return_value=memory), patch.object(agent, "client", client), \
                self.assertRaisesRegex(RuntimeError, "工作请求失败"):
            agent.agent_loop([{"role": "user", "content": "任务"}], "任务")
        memory.extract_memories.assert_not_called()

    def test_original_user_request_is_passed_to_extraction_after_compaction(self):
        memory = Mock()
        memory.load_memories.return_value = ""
        memory.extract_memories.return_value = 0
        client = Mock()
        client.messages.create.return_value = text_response("完成")
        with patch.object(agent, "MemoryManager", return_value=memory), patch.object(agent, "client", client), \
                patch.object(agent.ContextCompactor, "prepare", return_value=[{"role": "user", "content": "压缩后的背景"}]):
            messages = [{"role": "user", "content": "以后请用中文写注释。"}]
            agent.agent_loop(messages, "以后请用中文写注释。")
        self.assertEqual(memory.extract_memories.call_args.args[1], "以后请用中文写注释。")
        memory.consolidate_memories.assert_not_called()


if __name__ == "__main__":
    unittest.main()

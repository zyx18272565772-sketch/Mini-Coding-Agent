"""验证 task 工具的完整消息往返，以及父级钩子对委派的控制。"""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from anthropic import Anthropic
from anthropic.types import Message
import httpx2

import hook
from tool import workspace
from tests.memory_support import isolate_memory

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), \
        patch("dotenv.load_dotenv"):
    from agent import agentloop as parent
    from agent import subagent


class SubagentIntegrationTests(unittest.TestCase):
    def setUp(self):
        isolate_memory(self, parent)

    def test_task_returns_only_final_child_text_and_shares_written_files(self):
        requests = []
        delegated_prompt = "创建 child.txt 并读取验证，返回文件路径"
        internal_details = "只在子任务中处理的详细文件内容"
        summary = "已创建并读取验证 child.txt"

        def handle(request):
            body = json.loads(request.content)
            requests.append(body)
            step = len(requests)
            if step == 1:
                self.assertIn("task", {item["name"] for item in body["tools"]})
                content = [{"type": "tool_use", "id": "delegate", "name": "task",
                            "input": {"prompt": delegated_prompt}}]
            elif step == 2:
                self.assertEqual(body["messages"], [{"role": "user", "content": delegated_prompt}])
                self.assertEqual({item["name"] for item in body["tools"]},
                                 {"bash", "read_file", "write_file", "edit_file", "glob", "load_skill", "compact"})
                self.assertEqual(body["model"], requests[0]["model"])
                self.assertNotEqual(body["system"], requests[0]["system"])
                content = [{"type": "text", "text": "子任务的中间分析"},
                           {"type": "tool_use", "id": "child_write", "name": "write_file",
                            "input": {"path": "child.txt", "content": internal_details}}]
            elif step == 3:
                self.assertEqual(body["messages"][-1]["content"][0]["tool_use_id"], "child_write")
                content = [{"type": "tool_use", "id": "child_read", "name": "read_file",
                            "input": {"path": "child.txt"}}]
            elif step == 4:
                self.assertEqual(body["messages"][-1]["content"][0]["content"], internal_details)
                content = [{"type": "text", "text": summary}]
            else:
                self.assertEqual(step, 5)
                self.assertIn("task", {item["name"] for item in body["tools"]})
                self.assertEqual(body["messages"][-1], {"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": "delegate", "content": summary,
                }]})
                serialized = json.dumps(body["messages"], ensure_ascii=False)
                self.assertIn("仅主对话持有的背景", serialized)
                self.assertNotIn(internal_details, serialized)
                self.assertNotIn("child_write", serialized)
                self.assertNotIn("child_read", serialized)
                self.assertNotIn("子任务的中间分析", serialized)
                content = [{"type": "text", "text": "主任务完成"}]
            return httpx2.Response(200, json={
                "id": f"msg_{step}", "type": "message", "role": "assistant", "model": "test-model",
                "content": content,
                "stop_reason": "tool_use" if any(item["type"] == "tool_use" for item in content) else "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(workspace, "WORKDIR", Path(directory).resolve()), \
                Anthropic(
                    api_key="test-key", base_url="https://api.example.test", max_retries=0,
                    http_client=httpx2.Client(transport=httpx2.MockTransport(handle)),
                ) as client, patch.object(parent, "client", client), \
                patch.object(subagent, "client", client), patch("sys.stdout", io.StringIO()):
            messages = [
                {"role": "user", "content": "仅主对话持有的背景"},
                {"role": "assistant", "content": "已记录背景"},
                {"role": "user", "content": "使用子任务创建并检查文件"},
            ]
            parent.agent_loop(messages, messages[-1]["content"])
            self.assertEqual((Path(directory) / "child.txt").read_text(encoding="utf-8"), internal_details)
        self.assertEqual(len(requests), 5)
        self.assertEqual(len(messages), 6)
        self.assertEqual(messages[-1]["content"][0].text, "主任务完成")

    def test_parent_pre_hook_can_block_task_before_any_child_request(self):
        parent_client = Mock()
        child_client = Mock()
        parent_client.messages.create.side_effect = [
            Message(id="msg_task", type="message", role="assistant", model="test-model",
                    content=[{"type": "tool_use", "id": "delegate", "name": "task", "input": {"prompt": "检查文件"}}],
                    stop_reason="tool_use", usage={"input_tokens": 1, "output_tokens": 1}),
            Message(id="msg_done", type="message", role="assistant", model="test-model",
                    content=[{"type": "text", "text": "委派已取消"}],
                    stop_reason="end_turn", usage={"input_tokens": 1, "output_tokens": 1}),
        ]
        output = io.StringIO()
        with patch.dict(hook.HOOKS, {event: list(callbacks) for event, callbacks in hook.HOOKS.items()}, clear=True), \
                patch.object(parent, "client", parent_client), \
                patch.object(subagent, "client", child_client), patch("sys.stdout", output):
            hook.register_hook("PreToolUse", lambda block: "委派被拒绝" if block.name == "task" else None)
            messages = [{"role": "user", "content": "委派检查"}]
            parent.agent_loop(messages, messages[-1]["content"])
        child_client.messages.create.assert_not_called()
        self.assertEqual(messages[2]["content"], [{
            "type": "tool_result", "tool_use_id": "delegate", "content": "委派被拒绝",
        }])
        self.assertNotIn("子智能体开始", output.getvalue())


if __name__ == "__main__":
    unittest.main()

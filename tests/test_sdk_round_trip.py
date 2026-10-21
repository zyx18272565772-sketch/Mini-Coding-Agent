"""在不访问网络的情况下，验证 SDK 对模型回复和工具结果的序列化。"""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from anthropic import Anthropic
import httpx2

from tool import workspace
from task_system.store import TaskStore
from tests.memory_support import isolate_memory

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), \
        patch("dotenv.load_dotenv"):
    from agent import agentloop as agent


class SDKRoundTripTests(unittest.TestCase):
    def setUp(self):
        isolate_memory(self, agent)

    def test_real_sdk_sends_tool_result_to_next_model_request(self):
        requests = []

        def handle(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                content = [{"type": "tool_use", "id": "call_test", "name": "bash",
                            "input": {"command": "echo agent-loop-ok"}}]
            else:
                self.assertEqual(len(requests), 2)
                self.assertEqual(body["messages"][-1], {"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": "call_test", "content": "agent-loop-ok",
                }]})
                content = [{"type": "text", "text": "完成"}]
            return httpx2.Response(200, json={
                "id": "msg_test", "type": "message", "role": "assistant", "model": "test-model",
                "content": content, "stop_reason": "tool_use" if len(requests) == 1 else "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })

        with Anthropic(
            api_key="test-key", base_url="https://api.example.test", max_retries=0,
            http_client=httpx2.Client(transport=httpx2.MockTransport(handle)),
        ) as client, patch.object(agent, "client", client):
            messages = [{"role": "user", "content": "执行命令"}]
            self.assertIsNone(agent.agent_loop(messages, messages[-1]["content"]))

        self.assertEqual(len(requests), 2)
        self.assertEqual(messages[-1]["content"][0].text, "完成")

    def test_all_five_tools_share_workspace_and_return_results_in_call_order(self):
        requests = []
        args = [sys.executable, "nested/demo.py"]
        command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)

        def handle(request):
            body = json.loads(request.content)
            requests.append(body)
            step = len(requests)
            if step == 1:
                self.assertEqual({item["name"] for item in body["tools"]},
                                 {"bash", "read_file", "write_file", "edit_file", "glob", "load_skill", "compact", "task",
                                  "create_task", "update_task", "list_tasks", "get_task", "claim_task", "complete_task"})
                calls = [("write_file", {"path": "nested/demo.py", "content": 'print("before")\n'})]
            elif step == 2:
                self.assertEqual(body["messages"][-1]["content"][0]["tool_use_id"], "write_file")
                calls = [
                    ("edit_file", {"path": "nested/demo.py", "old_text": "before", "new_text": "after"}),
                    ("read_file", {"path": "nested/demo.py"}),
                    ("glob", {"pattern": "**/*.py"}),
                    ("bash", {"command": command}),
                ]
            else:
                self.assertEqual(step, 3)
                results = body["messages"][-1]["content"]
                self.assertEqual([item["tool_use_id"] for item in results],
                                 ["edit_file", "read_file", "glob", "bash"])
                self.assertEqual(results[1]["content"], 'print("after")')
                self.assertEqual(Path(results[2]["content"]).as_posix(), "nested/demo.py")
                self.assertEqual(results[3]["content"], "after")
                calls = []
            content = [
                {"type": "tool_use", "id": name, "name": name, "input": arguments}
                for name, arguments in calls
            ] if calls else [{"type": "text", "text": "完成"}]
            return httpx2.Response(200, json={
                "id": f"msg_{step}", "type": "message", "role": "assistant", "model": "test-model",
                "content": content, "stop_reason": "tool_use" if calls else "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(workspace, "WORKDIR", Path(directory).resolve()), \
                Anthropic(
                    api_key="test-key", base_url="https://api.example.test", max_retries=0,
                    http_client=httpx2.Client(transport=httpx2.MockTransport(handle)),
                ) as client, patch.object(agent, "client", client):
            messages = [{"role": "user", "content": "创建、修改、读取、查找并执行 Python 文件"}]
            agent.agent_loop(messages, messages[-1]["content"])
            self.assertEqual((Path(directory) / "nested/demo.py").read_text(encoding="utf-8"),
                             'print("after")\n')
        self.assertEqual(len(requests), 3)
        self.assertEqual(messages[-1]["content"][0].text, "完成")

    def test_task_list_and_details_are_serialized_with_real_ids(self):
        requests = []
        task_id = None

        def handle(request):
            nonlocal task_id
            body = json.loads(request.content)
            requests.append(body)
            step = len(requests)
            if step == 1:
                call_id, name, arguments = "create", "create_task", {"subject": "检查文件", "description": "确认文件内容正确"}
            elif step == 2:
                task_id = json.loads(body["messages"][-1]["content"][0]["content"])["id"]
                call_id, name, arguments = "list", "list_tasks", {}
            elif step == 3:
                self.assertIn(task_id, body["messages"][-1]["content"][0]["content"])
                call_id, name, arguments = "detail", "get_task", {"task_id": task_id}
            else:
                self.assertEqual(step, 4)
                results = body["messages"][-1]["content"]
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["tool_use_id"], "detail")
                self.assertEqual(json.loads(results[0]["content"])["description"], "确认文件内容正确")
            content = [{"type": "tool_use", "id": call_id, "name": name, "input": arguments}] \
                if step < 4 else [{"type": "text", "text": "完成"}]
            return httpx2.Response(200, json={
                "id": f"msg_{step}", "type": "message", "role": "assistant", "model": "test-model",
                "content": content, "stop_reason": "tool_use" if step < 4 else "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(workspace, "WORKDIR", Path(directory).resolve()), \
                Anthropic(
                    api_key="test-key", base_url="https://api.example.test", max_retries=0,
                    http_client=httpx2.Client(transport=httpx2.MockTransport(handle)),
                ) as client, patch.object(agent, "client", client):
            messages = [{"role": "user", "content": "创建任务，并查看任务列表和详情"}]
            agent.agent_loop(messages, messages[-1]["content"])
            self.assertEqual(TaskStore(Path(directory)).load(task_id).subject, "检查文件")
        self.assertEqual(len(requests), 4)
        self.assertEqual(messages[-1]["content"][0].text, "完成")


if __name__ == "__main__":
    unittest.main()

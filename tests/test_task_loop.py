"""真实 SDK 模拟回复驱动任务生命周期，验证工具结果与依赖规则。"""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from anthropic import Anthropic
import httpx2

import hook
from agent import events
from task_system.store import TaskStore
from tests.memory_support import isolate_memory
from tests.streaming_support import model_response
from tool import workspace

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), patch("dotenv.load_dotenv"):
    from agent import agentloop as agent


def call(call_id, name, **arguments):
    return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}


class TaskLoopTests(unittest.TestCase):
    def setUp(self):
        isolate_memory(self, agent)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        for patcher in (
            patch.object(workspace, "WORKDIR", self.root),
            patch.dict(hook.HOOKS, {name: list(items) for name, items in hook.HOOKS.items()}, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(events.configure)

    def run_agent(self, respond):
        def transport(request):
            return model_response(respond(json.loads(request.content)), stream=False)
        with Anthropic(api_key="test-key", base_url="https://example.test", max_retries=0,
                       http_client=httpx2.Client(transport=httpx2.MockTransport(transport))) as client, \
                patch.object(agent, "client", client):
            messages = [{"role": "user", "content": "实现设备查询接口，然后验证接口"}]
            agent.agent_loop(messages, messages[0]["content"])
            return messages

    def test_runtime_ids_dependency_claim_execution_and_completion(self):
        requests, ids = [], []

        def respond(body):
            requests.append(body)
            results = body["messages"][-1]["content"]
            step = len(requests)
            if step == 1:
                names = {item["name"] for item in body["tools"]}
                self.assertNotIn("todo_write", names)
                self.assertTrue({"create_task", "update_task", "list_tasks", "get_task", "claim_task", "complete_task"} <= names)
                return [call("a", "create_task", subject="实现接口"), call("b", "create_task", subject="验证接口")]
            self.assertTrue(all(block["type"] == "tool_result" for block in results), "不能再追加三轮待办提醒")
            if step == 2:
                ids.extend(json.loads(item["content"])["id"] for item in results)
                return [call("dep", "update_task", task_id=ids[1], addBlockedBy=[ids[0]]),
                        call("blocked", "claim_task", task_id=ids[1]), call("claim_a", "claim_task", task_id=ids[0])]
            if step == 3:
                self.assertEqual([item["tool_use_id"] for item in results], ["dep", "blocked", "claim_a"])
                self.assertIn("依赖尚未完成", results[1]["content"])
                return [call("write", "write_file", path="api.txt", content="接口实现")]
            if step == 4:
                return [call("done_a", "complete_task", task_id=ids[0]), call("claim_b", "claim_task", task_id=ids[1])]
            if step == 5:
                self.assertIn("验证接口", results[0]["content"])
                return [call("verify", "read_file", path="api.txt"), call("done_b", "complete_task", task_id=ids[1])]
            self.assertEqual(step, 6)
            self.assertEqual(results[0]["content"], "1 | 接口实现")
            return [{"type": "text", "text": "实现和验证均已完成"}]

        messages = self.run_agent(respond)
        self.assertEqual([TaskStore(self.root).load(task_id).status for task_id in ids], ["completed", "completed"])
        self.assertEqual(messages[-1]["content"][0].text, "实现和验证均已完成")

    def test_permission_denial_prevents_task_creation(self):
        hook.register_hook("PreToolUse", lambda block: "本次创建被拒绝" if block.name == "create_task" else None)
        responses = iter([[call("create", "create_task", subject="不应创建")], [{"type": "text", "text": "已停止"}]])
        messages = self.run_agent(lambda body: next(responses))
        self.assertEqual(messages[2]["content"][0]["content"], "本次创建被拒绝")
        self.assertFalse((self.root / ".tasks").exists())

    def test_removed_todo_tool_is_not_executed_even_with_old_model_call(self):
        responses = iter([[call("legacy", "todo_write", todos=[])], [{"type": "text", "text": "改用任务系统"}]])
        messages = self.run_agent(lambda body: next(responses))
        self.assertEqual(messages[2]["content"][0]["content"], "未知工具：todo_write")


if __name__ == "__main__":
    unittest.main()

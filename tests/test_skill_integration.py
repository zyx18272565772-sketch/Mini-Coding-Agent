"""使用真实 SDK 的本地模拟响应，验证主、子智能体的技能加载往返。"""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from anthropic import Anthropic
import httpx2

import hook
from skill.loader import SkillLoader
from tool import load_skill
from tests.memory_support import isolate_memory

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), \
        patch("dotenv.load_dotenv"):
    from agent import agentloop, prompts, subagent


class SkillIntegrationTests(unittest.TestCase):
    def setUp(self):
        isolate_memory(self, agentloop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / "skills"
        manifest = root / "review" / "SKILL.md"
        manifest.parent.mkdir(parents=True)
        self.full_content = "---\nname: review\ndescription: 检查代码问题\n---\n# 详细步骤\n核对每个异常分支的测试。\n"
        manifest.write_text(self.full_content, encoding="utf-8")
        loader = SkillLoader(root)
        for patcher in (
            patch.object(load_skill, "SKILL_LOADER", loader),
            patch.object(prompts, "SKILL_LOADER", loader),
            patch.dict(hook.HOOKS, {event: list(callbacks) for event, callbacks in hook.HOOKS.items()}, clear=True),
            patch("sys.stdout", io.StringIO()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        system = prompts.build_system_prompt()
        for patcher in (
            patch.object(agentloop, "SYSTEM", system),
            patch.object(subagent, "SUB_SYSTEM", system + "请执行子任务并返回结论。"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_loading(self, module, name="review", expected_output=None):
        requests = []
        expected_output = self.full_content if expected_output is None else expected_output

        def handle(request):
            body = json.loads(request.content)
            requests.append(body)
            self.assertIn("- review: 检查代码问题", body["system"])
            self.assertIn("load_skill", body["system"])
            self.assertNotIn("核对每个异常分支", body["system"])
            definition = next(item for item in body["tools"] if item["name"] == "load_skill")
            self.assertEqual(definition["input_schema"]["required"], ["name"])
            if len(requests) == 1:
                self.assertNotIn("核对每个异常分支", json.dumps(body["messages"], ensure_ascii=False))
                content = [{"type": "tool_use", "id": "load_review", "name": "load_skill",
                            "input": {"name": name}}]
            else:
                self.assertEqual(len(requests), 2)
                self.assertEqual(body["messages"][-1], {"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": "load_review", "content": expected_output,
                }]})
                content = [{"type": "text", "text": "已按说明完成检查"}]
            return httpx2.Response(200, json={
                "id": f"msg_{len(requests)}", "type": "message", "role": "assistant", "model": "test-model",
                "content": content, "stop_reason": "tool_use" if len(requests) == 1 else "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })

        with Anthropic(
            api_key="test-key", base_url="https://api.example.test", max_retries=0,
            http_client=httpx2.Client(transport=httpx2.MockTransport(handle)),
        ) as client, patch.object(module, "client", client):
            if module is agentloop:
                messages = [{"role": "user", "content": "加载审查技能并检查代码"}]
                agentloop.agent_loop(messages, messages[-1]["content"])
                self.assertEqual(messages[-1]["content"][0].text, "已按说明完成检查")
            else:
                self.assertEqual(subagent.run_subagent("加载审查技能并检查代码"), "已按说明完成检查")
        self.assertEqual(len(requests), 2)
        return requests

    def test_parent_receives_only_catalog_then_full_content_with_lifecycle_hooks(self):
        events = []
        hook.register_hook("PreToolUse", lambda block: events.append(("pre", block.name)))
        hook.register_hook("PostToolUse", lambda block, output: events.append(("post", output)))
        self.run_loading(agentloop)
        self.assertEqual(events, [("pre", "load_skill"), ("post", self.full_content)])

    def test_child_can_load_a_skill_in_its_own_messages(self):
        self.run_loading(subagent)

    def test_pre_hook_denial_keeps_full_instructions_out_of_messages(self):
        completed = []
        hook.register_hook("PreToolUse", lambda block: "技能加载被拒绝" if block.name == "load_skill" else None)
        hook.register_hook("PostToolUse", lambda block, output: completed.append(block.name))
        requests = self.run_loading(agentloop, expected_output="技能加载被拒绝")
        self.assertEqual(completed, [])
        self.assertNotIn("核对每个异常分支", json.dumps(requests, ensure_ascii=False))

    def test_unknown_skill_error_is_sent_back_and_loop_can_finish(self):
        self.run_loading(agentloop, name="missing", expected_output="错误：未知技能 'missing'。可用技能：review")


if __name__ == "__main__":
    unittest.main()

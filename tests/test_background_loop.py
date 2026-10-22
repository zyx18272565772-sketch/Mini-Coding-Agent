"""模型先结束回复时等待后台事件，收到真实结果后自动继续；不调用真实 API。"""

from copy import deepcopy
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import hook
from agent import events
from tests.memory_support import isolate_memory
from tests.test_basic_loop import response

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), patch("dotenv.load_dotenv"):
    from agent import agentloop, subagent


def call(call_id="bg_call", name="bash", **args):
    return {"type": "tool_use", "id": call_id, "name": name,
            "input": {"command": "echo result", "run_in_background": True, **args}}


class BackgroundLoopTests(unittest.TestCase):
    def setUp(self):
        self.memory = isolate_memory(self, agentloop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.client = Mock()
        self.recorded = []
        events.configure(lambda event: self.recorded.append(deepcopy(event)))
        self.addCleanup(events.configure)
        for patcher in (
            patch("tool.workspace.WORKDIR", self.root),
            patch.object(agentloop, "client", self.client),
            patch.object(subagent, "client", self.client),
            patch.dict(hook.HOOKS, {name: list(items) for name, items in hook.HOOKS.items()}, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_waits_without_model_polling_then_delivers_one_notification_and_final_answer(self):
        finish = threading.Event()
        self.addCleanup(finish.set)
        posts, stops, requests = [], [], []
        hook.register_hook("PostToolUse", lambda block, output: posts.append((block.id, output, threading.current_thread())))
        hook.register_hook("Stop", lambda messages: stops.append(len(messages)))

        def run(command, **kwargs):
            if not finish.wait(3):
                raise RuntimeError("主循环没有进入等待后台事件的分支")
            return "后台测试全部通过", 0

        def on_event(event):
            self.recorded.append(deepcopy(event))
            if event["type"] == "notice" and "等待后台" in event.get("text", ""):
                self.assertEqual(len(requests), 2, "等待期间不能反复调用模型")
                self.assertEqual(stops, [], "后台未交付前不能触发本轮 Stop")
                self.memory.extract_memories.assert_not_called()
                finish.set()

        def create(**kwargs):
            requests.append(deepcopy(kwargs["messages"]))
            if len(requests) == 1:
                return response([call(), {"type": "tool_use", "id": "read", "name": "read_file", "input": {"path": "readme.txt"}}])
            if len(requests) == 2:
                self.assertFalse(finish.is_set())
                self.assertEqual(requests[-1][-1]["content"][1]["content"], "1 | 独立文档")
                self.assertIn("已启动", requests[-1][-1]["content"][0]["content"])
                return response([{"type": "text", "text": "文档检查完成，等待测试结果。"}])
            self.assertEqual(len(requests), 3)
            self.assertIn("后台测试全部通过", requests[-1][-1]["content"][0]["text"])
            return response([{"type": "text", "text": "测试和文档都已检查完成"}])

        (self.root / "readme.txt").write_text("独立文档", encoding="utf-8")
        events.configure(on_event)
        self.client.messages.create.side_effect = create
        messages = [{"role": "user", "content": "后台测试并阅读文档，最后报告结果"}]
        with patch("agent.background.run_bash_process", side_effect=run):
            agentloop.agent_loop(messages, messages[0]["content"])
        self.assertEqual(messages[-1]["content"][0].text, "测试和文档都已检查完成")
        results = [item for message in messages if isinstance(message["content"], list)
                   for item in message["content"] if isinstance(item, dict) and item.get("type") == "tool_result"]
        self.assertEqual([item["tool_use_id"] for item in results], ["bg_call", "read"])
        bg_posts = [item for item in posts if item[0] == "bg_call"]
        self.assertEqual(len(bg_posts), 1)
        self.assertEqual(bg_posts[0][1], "后台测试全部通过")
        self.assertIs(bg_posts[0][2], threading.current_thread())
        self.assertEqual(len(stops), 1)
        self.memory.extract_memories.assert_called_once()
        self.assertEqual(len([event for event in self.recorded if event["type"] == "background_result"]), 1)

    def test_background_permission_denial_never_starts_a_thread(self):
        events.configure(self.recorded.append, lambda *args: "deny")
        self.client.messages.create.side_effect = [response([call(command="del data.txt")]), response([{"type": "text", "text": "已拒绝"}])]
        with patch("agent.background.run_bash_process") as run:
            messages = [{"role": "user", "content": "执行命令"}]
            agentloop.agent_loop(messages, "执行命令")
        run.assert_not_called()
        self.assertEqual(messages[2]["content"][0]["content"], "权限被拒绝。")
        self.assertFalse(any(event["type"] == "background_result" for event in self.recorded))

    def test_model_error_cancels_started_background_command(self):
        started, cancelled = threading.Event(), threading.Event()

        def run(command, *, cancel, **kwargs):
            started.set()
            if cancel.wait(3):
                cancelled.set()
            return "已停止", None

        count = 0
        def scripted(**kwargs):
            nonlocal count
            count += 1
            if count == 1:
                return response([call()])
            self.assertTrue(started.wait(2))
            raise RuntimeError("模型连接失败")

        self.client.messages.create.side_effect = scripted
        with patch("agent.background.run_bash_process", side_effect=run), self.assertRaisesRegex(RuntimeError, "模型连接失败"):
            agentloop.agent_loop([{"role": "user", "content": "后台命令"}], "后台命令")
        self.assertTrue(cancelled.is_set())
        self.memory.extract_memories.assert_not_called()

    def test_child_waits_for_its_own_background_result_before_returning_summary(self):
        finish = threading.Event()
        self.addCleanup(finish.set)
        requests = []

        def run(command, **kwargs):
            self.assertTrue(finish.wait(3))
            return "子任务测试通过", 0

        def on_event(event):
            self.recorded.append(deepcopy(event))
            if event["type"] == "notice" and "等待后台" in event.get("text", ""):
                finish.set()

        def create(**kwargs):
            requests.append(deepcopy(kwargs["messages"]))
            contents = [[call()], [{"type": "text", "text": "测试仍在后台运行"}], [{"type": "text", "text": "子任务最终结论"}]]
            return response(contents[len(requests) - 1])

        events.configure(on_event)
        self.client.messages.create.side_effect = create
        with patch("agent.background.run_bash_process", side_effect=run):
            self.assertEqual(subagent.run_subagent("后台执行子任务测试"), "子任务最终结论")
        self.assertEqual(len(requests), 3)
        self.assertIn("子任务测试通过", requests[-1][-1]["content"][0]["text"])
        self.assertTrue(all(event["scope"] == "subagent" for event in self.recorded))

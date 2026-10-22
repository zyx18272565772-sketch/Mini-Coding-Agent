"""验证压缩接入主、子循环后的消息往返、整批工具执行和错误重试。"""

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
from task_system.store import TaskStore
from tool.write_file import run_write
from tests.memory_support import isolate_memory

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), \
        patch("dotenv.load_dotenv"):
    from agent import agentloop as parent, subagent


def response(content):
    return Message(id="msg_test", type="message", role="assistant", model="test-model", content=content,
                   stop_reason="tool_use" if any(block["type"] == "tool_use" for block in content) else "end_turn",
                   usage={"input_tokens": 1, "output_tokens": 1})


def text_response(text):
    return response([{"type": "text", "text": text}])


def tool(call_id, name, **arguments):
    return {"type": "tool_use", "id": call_id, "name": name, "input": arguments}


class CompactLoopTests(unittest.TestCase):
    def setUp(self):
        isolate_memory(self, parent)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        for patcher in (
            patch.object(workspace, "WORKDIR", self.root),
            patch.dict(hook.HOOKS, {event: list(callbacks) for event, callbacks in hook.HOOKS.items()}, clear=True),
            patch("sys.stdout", io.StringIO()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def sdk_client(self, handler):
        def transport(request):
            reply = response(handler(json.loads(request.content)))
            return httpx2.Response(200, json=reply.model_dump(mode="json"))
        client = Anthropic(api_key="test-key", base_url="https://api.example.test", max_retries=0,
                           http_client=httpx2.Client(transport=httpx2.MockTransport(transport)))
        self.addCleanup(client.close)
        return client

    def test_compact_waits_for_all_tools_and_keeps_task_and_written_file(self):
        requests = []
        completed = []
        hook.register_hook("PostToolUse", lambda block, output: completed.append(block.id))
        request = "创建文件并记下进度，然后继续检查"
        task = TaskStore(self.root).create("创建文件")
        TaskStore(self.root).claim(task.id)

        def handle(body):
            requests.append(body)
            step = len(requests)
            if step == 1:
                self.assertIn("compact", {item["name"] for item in body["tools"]})
                return [tool("compact_first", "compact"),
                        tool("write_after", "write_file", path="stage.txt", content="已完成的文件内容"),
                        tool("plan", "complete_task", task_id=task.id)]
            if step == 2:
                self.assertNotIn("tools", body)
                self.assertEqual(body["max_tokens"], 2000)
                history = json.loads(body["messages"][0]["content"])
                self.assertEqual([block["tool_use_id"] for block in history[-1]["content"]],
                                 ["compact_first", "write_after", "plan"])
                self.assertEqual((self.root / "stage.txt").read_text(encoding="utf-8"), "已完成的文件内容")
                self.assertEqual(completed, ["compact_first", "write_after", "plan"])
                self.assertEqual(TaskStore(self.root).load(task.id).status, "completed")
                return [{"type": "text", "text": "文件已创建，任务已完成，接下来检查结果。"}]
            self.assertEqual(step, 3)
            self.assertEqual(len(body["messages"]), 1)
            self.assertIn(request, body["messages"][0]["content"])
            self.assertIn("文件已创建", body["messages"][0]["content"])
            return [{"type": "text", "text": "检查完成"}]

        client = self.sdk_client(handle)
        messages = [{"role": "user", "content": request}]
        original = messages
        with patch.object(parent, "client", client):
            parent.agent_loop(messages, request)
        self.assertIs(messages, original)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["content"][0].text, "检查完成")
        self.assertEqual(len(requests), 3)
        self.assertEqual(len(list((self.root / ".transcripts").glob("*.jsonl"))), 1)

    def test_child_can_compact_and_parent_still_receives_only_final_summary(self):
        requests = []
        private = "子任务内部读取的详细内容"
        (self.root / "details.txt").write_text(private, encoding="utf-8")

        def handle(body):
            requests.append(body)
            step = len(requests)
            if step == 1:
                return [tool("delegate", "task", prompt="独立检查 details.txt")]
            if step == 2:
                self.assertIn("compact", {item["name"] for item in body["tools"]})
                return [tool("read", "read_file", path="details.txt"), tool("compact_child", "compact")]
            if step == 3:
                self.assertNotIn("tools", body)
                self.assertIn(private, body["messages"][0]["content"])
                return [{"type": "text", "text": "details.txt 已检查，未发现问题。"}]
            if step == 4:
                self.assertIn("独立检查 details.txt", body["messages"][0]["content"])
                return [{"type": "text", "text": "子任务结论：文件正常。"}]
            self.assertEqual(step, 5)
            self.assertEqual(body["messages"][-1]["content"][0]["content"], "子任务结论：文件正常。")
            self.assertNotIn(private, json.dumps(body["messages"], ensure_ascii=False))
            self.assertNotIn("compact_child", json.dumps(body["messages"], ensure_ascii=False))
            return [{"type": "text", "text": "主任务完成"}]

        client = self.sdk_client(handle)
        messages = [{"role": "user", "content": "委派检查文件"}]
        with patch.object(parent, "client", client), patch.object(subagent, "client", client):
            parent.agent_loop(messages, "委派检查文件")
        self.assertEqual(len(requests), 5)
        self.assertEqual(messages[-1]["content"][0].text, "主任务完成")

    def test_pre_hook_denial_blocks_compaction_but_allows_other_tools(self):
        hook.register_hook("PreToolUse", lambda block: "不允许本次压缩" if block.name == "compact" else None)
        completed = []
        hook.register_hook("PostToolUse", lambda block, output: completed.append(block.name))
        client = Mock()
        client.messages.create.side_effect = [
            response([tool("compact", "compact"), tool("write", "write_file", path="ok.txt", content="保留")]),
            text_response("完成"),
        ]
        with patch.object(parent, "client", client):
            messages = [{"role": "user", "content": "完成任务"}]
            parent.agent_loop(messages, "完成任务")
        self.assertEqual(client.messages.create.call_count, 2)
        self.assertEqual(messages[2]["content"][0]["content"], "不允许本次压缩")
        self.assertEqual(completed, ["write_file"])
        self.assertEqual((self.root / "ok.txt").read_text(encoding="utf-8"), "保留")
        self.assertFalse((self.root / ".transcripts").exists())

    def test_automatic_summary_happens_before_regular_model_request(self):
        requests = []

        def handle(body):
            requests.append(body)
            if len(requests) == 1:
                self.assertNotIn("tools", body)
                self.assertEqual(body["max_tokens"], 2000)
                return [{"type": "text", "text": "此前已分析旧资料。"}]
            self.assertEqual(len(requests), 2)
            self.assertIn("继续实现当前任务", body["messages"][0]["content"])
            self.assertIn("此前已分析旧资料", body["messages"][0]["content"])
            self.assertIn("tools", body)
            return [{"type": "text", "text": "完成"}]

        with patch.object(parent, "client", self.sdk_client(handle)):
            messages = [{"role": "user", "content": "旧资料" * 20000}]
            parent.agent_loop(messages, "继续实现当前任务")
        self.assertEqual(len(requests), 2)

    def test_oversized_new_tool_result_is_persisted_before_next_request(self):
        original = "文件详细内容" * 20000
        requests = []

        def handle(body):
            requests.append(body)
            if len(requests) == 1:
                return [tool("large_output", "bash", command="fake-large-output")]
            self.assertEqual(len(requests), 2)
            result = body["messages"][-1]["content"][0]
            self.assertEqual(result["tool_use_id"], "large_output")
            self.assertIn("<persisted-output>", result["content"])
            self.assertIn(original[:1000], result["content"])
            return [{"type": "text", "text": "已读取预览"}]

        # read_file 现在主动分页；模拟其它工具的大输出，继续验证压缩层的兜底。
        with patch.object(parent, "client", self.sdk_client(handle)), \
                patch.dict(parent.TOOL_HANDLERS, {"bash": lambda command: original}):
            messages = [{"role": "user", "content": "读取大文件"}]
            parent.agent_loop(messages, "读取大文件")
        saved = self.root / ".task_outputs" / "tool-results" / "large_output.txt"
        self.assertEqual(saved.read_text(encoding="utf-8"), original)
        self.assertEqual(len(requests), 2)

    def test_context_error_retries_once_without_repeating_completed_write(self):
        client = Mock()
        client.messages.create.side_effect = [
            response([tool("write", "write_file", path="once.txt", content="只写一次")]),
            RuntimeError("prompt_too_long"), text_response("文件已写入，继续检查。"), text_response("完成"),
        ]
        writer = Mock(wraps=run_write)
        with patch.object(parent, "client", client), patch.dict(parent.TOOL_HANDLERS, {"write_file": writer}):
            messages = [{"role": "user", "content": "写入并检查文件"}]
            parent.agent_loop(messages, "写入并检查文件")
        self.assertEqual(client.messages.create.call_count, 4)
        writer.assert_called_once()
        self.assertIn("写入并检查文件", messages[0]["content"])
        self.assertEqual(messages[-1]["content"][0].text, "完成")

    def test_second_consecutive_context_error_propagates_in_both_loops(self):
        for module in (parent, subagent):
            with self.subTest(module=module.__name__):
                client = Mock()
                client.messages.create.side_effect = [
                    RuntimeError("too many tokens"), text_response("状态摘要"), RuntimeError("too many tokens"),
                ]
                with patch.object(module, "client", client), self.assertRaisesRegex(RuntimeError, "too many tokens"):
                    if module is parent:
                        module.agent_loop([{"role": "user", "content": "任务"}], "任务")
                    else:
                        module.run_subagent("任务")
                self.assertEqual(client.messages.create.call_count, 3)

    def test_successful_working_request_resets_reactive_retry_counter(self):
        client = Mock()
        client.messages.create.side_effect = [
            RuntimeError("prompt_too_long"), text_response("第一次摘要"),
            response([tool("read", "glob", pattern="*.txt")]),
            RuntimeError("prompt_too_long"), text_response("第二次摘要"), text_response("完成"),
        ]
        with patch.object(parent, "client", client):
            messages = [{"role": "user", "content": "查找文件"}]
            parent.agent_loop(messages, "查找文件")
        self.assertEqual(client.messages.create.call_count, 6)
        self.assertEqual(messages[-1]["content"][0].text, "完成")


if __name__ == "__main__":
    unittest.main()

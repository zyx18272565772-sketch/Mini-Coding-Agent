"""验证主子 Agent 的真实 SSE 往返，以及停止、失败后的部分文字保存。"""

import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from anthropic import Anthropic, APIConnectionError
import httpx2

from agent import events
from desktop_app.service import DesktopService
from tests.memory_support import isolate_memory
from tests.streaming_support import encode_event, message_events, model_response
from tool import workspace

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), patch("dotenv.load_dotenv"):
    from agent import agentloop as parent, subagent


def partial_worker(connection, payload):
    connection.send({"type": "assistant_start", "scope": "main", "message_id": "partial"})
    connection.send({"type": "assistant_delta", "scope": "main", "message_id": "partial", "text": "已生成的部分内容"})
    if payload["messages"][-1]["content"] == "模拟断线":
        connection.send({"type": "failure", "message": "模拟连接中断"})
        connection.close()
    else:
        connection.recv()


class StreamingLoopTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        isolate_memory(self, parent)
        self.recorded = []
        events.configure(self.recorded.append, streaming=True)
        self.addCleanup(events.configure)
        for patcher in (patch.object(workspace, "WORKDIR", self.root), patch("sys.stdout", io.StringIO())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def client(self, transport):
        client = Anthropic(api_key="test-key", base_url="https://example.test", max_retries=0,
                           http_client=httpx2.Client(transport=httpx2.MockTransport(transport)))
        self.addCleanup(client.close)
        return client

    def test_tool_waits_for_stream_completion_and_text_is_not_emitted_twice(self):
        writer = Mock(return_value="已写入")
        requests = []
        first = [{"type": "text", "text": "开始创建文件。"},
                 {"type": "tool_use", "id": "write", "name": "write_file", "input": {"path": "a.txt", "content": "中文"}}]

        class Body(httpx2.SyncByteStream):
            def __iter__(inner):
                for event in message_events(first):
                    writer.assert_not_called()
                    yield encode_event(event)

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=Body())
            self.assertEqual(body["messages"][-1]["content"][0]["tool_use_id"], "write")
            return model_response([{"type": "text", "text": "创建完成。"}], stream=True)

        messages = [{"role": "user", "content": "创建文件"}]
        with patch.object(parent, "client", self.client(respond)), patch.dict(parent.TOOL_HANDLERS, {"write_file": writer}):
            parent.agent_loop(messages, "创建文件")
        writer.assert_called_once_with(path="a.txt", content="中文")
        self.assertEqual([event["text"] for event in self.recorded if event["type"] == "assistant"],
                         ["开始创建文件。", "创建完成。"])
        self.assertEqual(sum(event["type"] == "assistant_start" for event in self.recorded), 2)

    def test_child_stream_has_its_own_scope_and_only_final_text_returns_to_parent(self):
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                content = [{"type": "tool_use", "id": "child", "name": "task", "input": {"prompt": "调查项目"}}]
            elif len(requests) == 2:
                content = [{"type": "text", "text": "子任务结论"}]
            else:
                self.assertEqual(body["messages"][-1]["content"][0]["content"], "子任务结论")
                content = [{"type": "text", "text": "主任务完成"}]
            return model_response(content, stream=True)

        client = self.client(respond)
        with patch.object(parent, "client", client), patch.object(subagent, "client", client):
            parent.agent_loop([{"role": "user", "content": "委派调查"}], "委派调查")
        self.assertEqual({event["scope"] for event in self.recorded if event["type"] == "assistant_delta"}, {"main", "subagent"})
        self.assertEqual([event["text"] for event in self.recorded if event["type"] == "assistant"], ["子任务结论", "主任务完成"])

    def test_incomplete_stream_does_not_execute_or_checkpoint_half_a_tool_call(self):
        content = [{"type": "text", "text": "准备写入。"},
                   {"type": "tool_use", "id": "write", "name": "write_file", "input": {"path": "a.txt", "content": "中文"}}]
        body = b"".join(encode_event(event) for event in message_events(content, complete=False))
        client = self.client(lambda request: httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body))
        writer = Mock()
        messages = [{"role": "user", "content": "创建文件"}]
        with patch.object(parent, "client", client), patch.dict(parent.TOOL_HANDLERS, {"write_file": writer}), \
                self.assertRaisesRegex(RuntimeError, "完整结束"):
            parent.agent_loop(messages, "创建文件")
        writer.assert_not_called()
        self.assertEqual(len(messages), 1)
        checkpoints = [event["messages"] for event in self.recorded if event["type"] == "checkpoint"]
        self.assertEqual(checkpoints, [[{"role": "user", "content": "创建文件"}]], "失败恢复只保存合法历史，不能保存半截工具调用")

    def test_failed_model_request_saves_background_cancellation_before_resume(self):
        requests = []

        def respond(request):
            requests.append(request)
            if len(requests) == 1:
                return model_response([{"type": "tool_use", "id": "background", "name": "bash",
                                        "input": {"command": "echo test", "run_in_background": True}}], stream=True)
            raise httpx2.ConnectError("模拟连接失败", request=request)

        def background_process(command, *, cwd, cancel):
            self.assertTrue(cancel.wait(timeout=3), "请求失败时应清理后台命令")
            return "已取消后台命令", -1

        messages = [{"role": "user", "content": "后台测试"}]
        with patch.object(parent, "client", self.client(respond)), \
                patch("agent.background.run_bash_process", side_effect=background_process), \
                self.assertRaises(APIConnectionError):
            parent.agent_loop(messages, "后台测试")
        results = [event for event in self.recorded if event["type"] == "background_result"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "cancelled")
        saved = [event["messages"] for event in self.recorded if event["type"] == "checkpoint"][-1]
        notifications = [block["text"] for block in saved[-1]["content"] if block["type"] == "text"]
        self.assertIn('"status": "cancelled"', notifications[0])
        self.assertEqual(sum(block["type"] == "tool_result" for block in saved[-1]["content"]), 1)


class StreamingServiceTests(unittest.TestCase):
    def test_stop_and_error_keep_visible_text_without_adding_partial_model_messages(self):
        for query, terminal in (("等待停止", "stopped"), ("模拟断线", "error")):
            with self.subTest(query=query), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                app = DesktopService(root, root / "sessions", worker_target=partial_worker)
                try:
                    sid = app.create_session()["data"]["id"]
                    with patch.object(app._store, "save", wraps=app._store.save) as save:
                        app.send_message(sid, query)
                        deadline = time.monotonic() + 8
                        while time.monotonic() < deadline:
                            session = app.get_session(sid)["data"]
                            if any(event["type"] == "assistant_delta" for event in session["events"]):
                                break
                            time.sleep(0.02)
                        self.assertTrue(any(event["type"] == "assistant_delta" for event in session["events"]))
                        if terminal == "stopped":
                            self.assertEqual(save.call_count, 2, "片段不能触发逐次磁盘写入")
                            app.stop(sid)
                    deadline = time.monotonic() + 8
                    while app.get_session(sid)["data"]["status"] != terminal and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertEqual(app.get_session(sid)["data"]["status"], terminal)
                    app.shutdown()
                    restored = DesktopService(root, root / "sessions", worker_target=partial_worker)
                    try:
                        session = restored.get_session(sid)["data"]
                        self.assertTrue(any(event.get("text") == "已生成的部分内容" for event in session["events"]))
                        self.assertEqual([message["role"] for message in restored._sessions[sid]["messages"]], ["user"])
                    finally:
                        restored.shutdown()
                finally:
                    app.shutdown()


if __name__ == "__main__":
    unittest.main()

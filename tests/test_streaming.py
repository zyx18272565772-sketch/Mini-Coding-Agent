"""使用真实 SDK 与模拟 SSE 验证逐段输出和完整工具参数。"""

import json
import unittest
from unittest.mock import Mock

from anthropic import Anthropic
import httpx2

from llm.generation import create_message
from tests.streaming_support import encode_event, message_events, model_response


class StreamingTests(unittest.TestCase):
    def client(self, handler):
        client = Anthropic(api_key="test-key", base_url="https://example.test", max_retries=0,
                           http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
        self.addCleanup(client.close)
        return client

    def request(self, client, callback):
        return create_message(client, model="test-model", messages=[{"role": "user", "content": "你好"}],
                              max_tokens=8000, on_event=callback)

    def test_text_is_delivered_before_the_final_message_arrives(self):
        recorded = []

        class LiveBody(httpx2.SyncByteStream):
            def __iter__(inner):
                for event in message_events([{"type": "text", "text": "你好，正在逐段生成。"}]):
                    if event["type"] == "message_stop":
                        self.assertTrue(any(item[0] == "assistant_delta" for item in recorded))
                        self.assertFalse(any(item[0] == "assistant" for item in recorded))
                    yield encode_event(event)

        def transport(request):
            self.assertTrue(json.loads(request.content)["stream"])
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=LiveBody())

        response = self.request(self.client(transport), lambda kind, **data: recorded.append((kind, data)))
        self.assertEqual(response.content[0].text, "你好，正在逐段生成。")
        self.assertEqual(recorded[0][0], "assistant_start")
        self.assertEqual(recorded[-1][0], "assistant")
        self.assertEqual(recorded[-1][1]["text"], response.content[0].text)
        self.assertEqual(len({item[1]["message_id"] for item in recorded}), 1)

    def test_tool_only_response_returns_complete_json_without_an_empty_text_bubble(self):
        events = []
        content = [{"type": "tool_use", "id": "write", "name": "write_file",
                    "input": {"path": "中文.txt", "content": "完整内容"}}]
        client = self.client(lambda request: model_response(content, stream=True))
        response = self.request(client, lambda *args, **data: events.append((args, data)))
        self.assertEqual(response.content[0].input, content[0]["input"])
        self.assertEqual(events, [])

    def test_multiple_text_blocks_have_the_same_final_text_as_non_streaming(self):
        recorded = []
        content = [{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"}]
        self.request(self.client(lambda request: model_response(content, stream=True)),
                     lambda kind, **data: recorded.append((kind, data)))
        self.assertEqual(recorded[-1][1]["text"], "第一段\n第二段")

    def test_interrupted_stream_preserves_visible_text_and_raises(self):
        recorded = []
        content = [{"type": "text", "text": "已生成的内容"}]
        body = b"".join(encode_event(event) for event in message_events(content, complete=False))
        client = self.client(lambda request: httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body))
        with self.assertRaisesRegex(RuntimeError, "完整结束"):
            self.request(client, lambda kind, **data: recorded.append((kind, data)))
        self.assertEqual(recorded[-1][0], "assistant_end")
        self.assertEqual(recorded[-1][1]["text"], "已生成的内容")
        self.assertFalse(any(kind == "assistant" for kind, _ in recorded))

    def test_incomplete_tool_stream_is_not_returned_for_execution(self):
        content = [{"type": "tool_use", "id": "write", "name": "write_file", "input": {"path": "a.txt"}}]
        body = b"".join(encode_event(event) for event in message_events(content, complete=False))
        client = self.client(lambda request: httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body))
        with self.assertRaisesRegex(RuntimeError, "完整结束"):
            self.request(client, lambda *args, **data: None)

    def test_token_limit_does_not_execute_potentially_truncated_tool_arguments(self):
        content = [{"type": "tool_use", "id": "write", "name": "write_file", "input": {"path": "a.txt"}}]
        events = list(message_events(content))
        for event in events:
            if event["type"] == "message_delta":
                event["delta"]["stop_reason"] = "max_tokens"
        body = b"".join(encode_event(event) for event in events)
        client = self.client(lambda request: httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body))
        with self.assertRaisesRegex(RuntimeError, "达到上限"):
            self.request(client, lambda *args, **data: None)

    def test_ordinary_calls_keep_the_existing_create_path(self):
        client = Mock()
        self.request(client, None)
        client.messages.create.assert_called_once()
        client.messages.stream.assert_not_called()


if __name__ == "__main__":
    unittest.main()

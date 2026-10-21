"""Anthropic SSE 测试数据，只通过 MockTransport 使用，不访问网络。"""

import json

import httpx2


def encode_event(event):
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")


def message_events(content, *, complete=True):
    yield {"type": "message_start", "message": {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "test-model",
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 0},
    }}
    for index, block in enumerate(content):
        start = {**block, "text": ""} if block["type"] == "text" else {**block, "input": {}}
        yield {"type": "content_block_start", "index": index, "content_block": start}
        value = block["text"] if block["type"] == "text" else json.dumps(block["input"], ensure_ascii=False)
        # 分割中文、文本和 JSON，确保测试不依赖一次收到完整内容。
        midpoint = max(1, len(value) // 2)
        for chunk in (value[:midpoint], value[midpoint:]):
            delta = {"type": "text_delta", "text": chunk} if block["type"] == "text" else {"type": "input_json_delta", "partial_json": chunk}
            yield {"type": "content_block_delta", "index": index, "delta": delta}
        yield {"type": "content_block_stop", "index": index}
    if complete:
        yield {"type": "message_delta", "delta": {
            "stop_reason": "tool_use" if any(block["type"] == "tool_use" for block in content) else "end_turn",
            "stop_sequence": None,
        }, "usage": {"output_tokens": 1}}
        yield {"type": "message_stop"}


def model_response(content, *, stream=False):
    if stream:
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                              content=b"".join(encode_event(event) for event in message_events(content)))
    return httpx2.Response(200, json={
        "id": "msg_test", "type": "message", "role": "assistant", "model": "test-model",
        "content": content,
        "stop_reason": "tool_use" if any(block["type"] == "tool_use" for block in content) else "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })

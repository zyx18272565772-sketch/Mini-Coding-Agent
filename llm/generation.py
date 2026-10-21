"""工作模型请求：可逐段报告文字，完整接收后再返回工具调用。"""

import time
from uuid import uuid4

from llm.errors import IncompleteStreamError
from llm.usage import current_run


def create_message(client, *, on_event=None, purpose="main", **kwargs):
    run = current_run()
    if run is not None:
        run.begin(purpose)
    usage = None
    complete = False
    try:
        def observe(value, final):
            nonlocal usage, complete
            usage, complete = value, final

        response = _create_message(client, on_event=on_event, observe=observe, **kwargs)
        usage, complete = getattr(response, "usage", None), True
        return response
    finally:
        if run is not None:
            run.finish(purpose, usage, complete=complete)


def _create_message(client, *, on_event=None, observe, **kwargs):
    if on_event is None:
        return client.messages.create(**kwargs)

    message_id = uuid4().hex
    started = False
    parts = []
    pending = ""
    last_flush = 0.0

    def flush():
        nonlocal pending, last_flush, started
        if not pending:
            return
        if not started:
            on_event("assistant_start", message_id=message_id)
            started = True
        on_event("assistant_delta", message_id=message_id, text=pending)
        pending = ""
        last_flush = time.monotonic()

    try:
        complete = False
        with client.messages.stream(**kwargs) as stream:
            for event in stream:
                if event.type == "message_start":
                    observe(event.message.usage, False)
                elif event.type == "message_delta":
                    observe(stream.current_message_snapshot.usage, False)
                if event.type == "content_block_delta" and event.delta.type == "text_delta":
                    text = event.delta.text
                    parts.append(text)
                    pending += text
                    if not started or time.monotonic() - last_flush >= 0.08 or len(pending) >= 256:
                        flush()
                elif event.type == "message_stop":
                    complete = True
            if not complete:
                raise IncompleteStreamError("模型输出流未完整结束，本轮工具不会执行。")
            response = stream.get_final_message()
            observe(response.usage, True)
        if response.stop_reason == "max_tokens" and any(block.type == "tool_use" for block in response.content):
            raise RuntimeError("模型输出达到上限，工具参数可能未完整接收，本轮工具不会执行。")
        flush()
        text = "\n".join(block.text for block in response.content if block.type == "text")
        if text:
            if not started:
                on_event("assistant_start", message_id=message_id)
            on_event("assistant", message_id=message_id, text=text)
        elif started:
            on_event("assistant_end", message_id=message_id, text="", interrupted=False)
        return response
    except BaseException:
        flush()
        if started:
            on_event("assistant_end", message_id=message_id, text="".join(parts), interrupted=True)
        raise

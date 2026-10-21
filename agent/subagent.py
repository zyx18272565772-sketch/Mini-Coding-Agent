"""使用独立聊天记录执行子任务，只向主智能体返回最终文本。"""

from agent.events import emit, streaming_enabled, subagent_scope, usage_reporter
from agent.background import BackgroundManager, inject_background_results, wait_for_background
from agent.prompts import SUB_SYSTEM
from context.compactor import ContextCompactor, MAX_REACTIVE_RETRIES
from hook import trigger_hooks
from llm.client import MODEL, client
from llm.generation import create_message
from llm.usage import run_usage
from tool import workspace
from tool.compact import COMPACT_REQUESTED
from tool.executor import execute_tool
from tool.registry import BASE_HANDLERS, BASE_TOOLS


SUB_TOOLS = list(BASE_TOOLS)
SUB_HANDLERS = dict(BASE_HANDLERS)


def extract_text(content) -> str:
    if not isinstance(content, list):
        return str(content)
    return "\n".join(
        block.text for block in content if getattr(block, "type", None) == "text"
    )


def run_subagent(prompt: str) -> str:
    with subagent_scope(), run_usage(report=usage_reporter()):
        return _run_subagent(prompt)


def _run_subagent(prompt: str) -> str:
    with BackgroundManager(workspace.WORKDIR) as background:
        return _run_subagent_loop(prompt, background)


def _run_subagent_loop(prompt: str, background: BackgroundManager) -> str:
    print("\n[子智能体开始]")
    messages = [{"role": "user", "content": prompt}]
    compactor = ContextCompactor(
        client, MODEL, workspace.WORKDIR / ".transcripts",
        workspace.WORKDIR / ".task_outputs" / "tool-results",
    )
    reactive_retries = 0
    while True:
        inject_background_results(messages, background)
        messages[:] = compactor.prepare(messages, prompt)
        try:
            response = create_message(
                client, on_event=emit if streaming_enabled() else None,
                purpose="subagent",
                model=MODEL, system=SUB_SYSTEM, messages=messages,
                tools=SUB_TOOLS, max_tokens=8000,
            )
            reactive_retries = 0
        except Exception as error:
            too_long = any(text in str(error).lower() for text in ("prompt_too_long", "too many tokens"))
            if too_long and reactive_retries < MAX_REACTIVE_RETRIES:
                print("[上下文] 子任务请求过长，压缩后重试")
                messages[:] = compactor.reactive_compact(messages, prompt)
                reactive_retries += 1
                continue
            raise
        messages.append({"role": "assistant", "content": response.content})
        text = extract_text(response.content)
        if text and not streaming_enabled():
            emit("assistant", text=text)
        tool_calls = [block for block in response.content if block.type == "tool_use"]
        if not tool_calls:
            if wait_for_background(messages, background):
                continue
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({"role": "user", "content": force})
                continue
            print("[子智能体完成]")
            return extract_text(response.content) or "（子智能体未返回总结）"

        results = []
        compact_requested = False
        for block in tool_calls:
            output, allowed = execute_tool(block, SUB_HANDLERS, background=background)
            if allowed and block.name == "compact" and output == COMPACT_REQUESTED:
                compact_requested = True
            print(f"  [子工具] {block.name}：{output[:100]}")
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })
        messages.append({"role": "user", "content": results})
        if compact_requested:
            messages[:] = compactor.compact_history(messages, prompt)


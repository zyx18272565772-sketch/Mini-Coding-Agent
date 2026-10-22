"""智能体主循环。"""

from agent.events import emit, streaming_enabled, usage_reporter
from agent.background import BackgroundManager, inject_background_results, wait_for_background
from agent.prompts import SYSTEM
from context.compactor import ContextCompactor, MAX_REACTIVE_RETRIES
from hook import trigger_hooks
from llm.client import MODEL, client
from llm.errors import describe_failure
from llm.generation import create_message
from llm.usage import run_usage
from memory.manager import MemoryManager
from storage.factory import memory_store
from mcp_client.runtime import github_mcp_scope, extend_tools
from tool import workspace
from tool.compact import COMPACT_REQUESTED
from tool.executor import execute_tool
from tool.registry import TOOLS, TOOL_HANDLERS


def agent_loop(messages: list, active_request: str, *, previous_usage=None, run_id=None):
    run = None
    background = None
    try:
        with run_usage(previous=previous_usage, report=usage_reporter(), run_id=run_id) as run:
            with github_mcp_scope() as mcp, BackgroundManager(workspace.WORKDIR) as background:
                _agent_loop(messages, active_request, background, mcp)
    except Exception as error:
        if describe_failure(error)["retryable"]:
            # 后台管理器已经清理命令；保存取消/完成通知，恢复时不会误以为仍在运行。
            if background is not None:
                inject_background_results(messages, background)
            emit("checkpoint", messages=messages)
        raise
    finally:
        if run is not None:
            usage = run.snapshot()
            unknown = f"，另有 {usage['unknown_calls']} 次用量不完整" if usage["unknown_calls"] else ""
            print(f"[用量] {usage['calls']} 次请求，已知输入 {usage['input_tokens']:,}，"
                  f"输出 {usage['output_tokens']:,}，总计 {usage['total_tokens']:,} Token{unknown}")


def _agent_loop(messages: list, active_request: str, background: BackgroundManager, mcp=None):
    tools, handlers = extend_tools(TOOLS, TOOL_HANDLERS, mcp)
    memory = MemoryManager(
        memory_store(workspace.WORKDIR), client, MODEL,
        report=lambda text: emit("notice", text=text),
    )
    #每次用户请求只召回一次，工具循环复用这份系统提示词。
    system = SYSTEM + memory.load_memories(messages, active_request)
    compactor = ContextCompactor(
        client, MODEL, workspace.WORKDIR / ".transcripts",
        workspace.WORKDIR / ".task_outputs" / "tool-results",
    )
    reactive_retries = 0
    while True:
        if inject_background_results(messages, background):
            emit("checkpoint", messages=messages)
        messages[:] = compactor.prepare(messages, active_request)
        try:
            response = create_message(
                client, on_event=emit if streaming_enabled() else None,
                model=MODEL, system=system, messages=messages,
                tools=tools, max_tokens=8000,
            )
            reactive_retries = 0
        except Exception as error:
            too_long = any(text in str(error).lower() for text in ("prompt_too_long", "too many tokens"))
            if too_long and reactive_retries < MAX_REACTIVE_RETRIES:
                print("[上下文] 请求过长，压缩后重试")
                messages[:] = compactor.reactive_compact(messages, active_request)
                reactive_retries += 1
                continue
            raise

        # 保存模型回复，包括它请求执行的工具调用。
        messages.append({"role": "assistant", "content": response.content})
        text = "\n".join(block.text for block in response.content if block.type == "text")
        if text and not streaming_enabled():
            emit("assistant", text=text)
        tool_calls = [block for block in response.content if block.type == "tool_use"]
        if not tool_calls:
            emit("checkpoint", messages=messages)
            if wait_for_background(messages, background):
                emit("checkpoint", messages=messages)
                continue
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({"role": "user", "content": force})
                continue
            if memory.extract_memories(messages, active_request):
                memory.consolidate_memories()
            return

        # 执行工具，将每个结果与原调用 ID 对应。
        results = []
        compact_requested = False
        for block in tool_calls:
            output, allowed = execute_tool(block, handlers, background=background)
            if allowed and block.name == "compact" and output == COMPACT_REQUESTED:
                compact_requested = True
            result = {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(output),
            }
            if getattr(output, "is_error", False) or (block.name.startswith("mcp__") and not allowed):
                result["is_error"] = True
            results.append(result)

        # 回传工具结果，再次调用模型。
        messages.append({"role": "user", "content": results})
        emit("checkpoint", messages=messages)
        if compact_requested:
            messages[:] = compactor.compact_history(messages, active_request)
            emit("checkpoint", messages=messages)

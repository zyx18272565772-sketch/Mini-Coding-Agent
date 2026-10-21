"""主智能体和子智能体共用的工具执行流程。"""

from hook import trigger_hooks
from agent.events import emit


def execute_tool(block, handlers: dict, *, background=None) -> tuple[str, bool]:
    """返回结果文本和是否通过执行前钩子，主循环据此处理压缩请求。"""
    emit("tool_start", call_id=block.id, name=block.name, args=block.input)
    blocked = trigger_hooks("PreToolUse", block)
    if blocked is not None:
        emit("tool_result", call_id=block.id, name=block.name, output=str(blocked), allowed=False)
        return str(blocked), False

    handler = handlers.get(block.name)
    try:
        if handler is not None and block.name == "bash" and block.input.get("run_in_background") is True:
            if background is None:
                raise ValueError("当前工具调用没有后台任务管理器")
            task_id = background.start(block)
            output = f"后台任务 {task_id} 已启动，尚未完成。完成后系统会自动交付结果，请继续处理独立工作。"
            print(f"[后台] 已启动 {task_id}：{block.input['command'][:60]}")
            emit("tool_result", call_id=block.id, name=block.name, output=output,
                 allowed=True, background=True, task_id=task_id)
            return output, True
        output = handler(**block.input) if handler else f"未知工具：{block.name}"
    except Exception as exc:
        output = f"错误：{exc}"
    trigger_hooks("PostToolUse", block, output)
    emit("tool_result", call_id=block.id, name=block.name, output=str(output), allowed=True)
    return str(output), True

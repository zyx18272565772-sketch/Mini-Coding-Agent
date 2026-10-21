"""将一个明确的子任务交给子智能体，同步等待最终结果。"""


def run_task(prompt: str) -> str:
    # 执行时再加载子智能体，避免工具注册时加载模型配置和循环导入。
    from agent.subagent import run_subagent

    return run_subagent(prompt)

"""智能体的交互式命令行入口。"""

import sys

from anthropic import AnthropicError

from hook import trigger_hooks


def main() -> int:
    # 模型模块会在导入时读取配置，在入口处处理配置缺失。
    try:
        from agent.agentloop import agent_loop
    except (KeyError, AnthropicError):
        print("请在 .env 或环境变量中填写 ANTHROPIC_API_KEY 和 MODEL_ID。", file=sys.stderr)
        return 1

    print("编程智能体 — 输入任务开始，输入 q / quit / exit 退出。\n")
    messages = []
    from pathlib import Path
    from storage.cli import cli_history
    try:
        history = cli_history(Path.cwd())
    except (ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1

    while True:
        try:
            query = input("你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if query.lower() in {"q", "quit", "exit"}:
            return 0
        if not query:
            continue

        try:
            trigger_hooks("UserPromptSubmit", query)
            messages.append({"role": "user", "content": query})
            if history is None:
                agent_loop(messages, query)
            else:
                with history.run(messages, query) as run_id:
                    agent_loop(messages, query, run_id=run_id)
        except KeyboardInterrupt:
            print("\n任务已中断。")
            return 130
        except AnthropicError as exc:
            print(f"\n模型请求失败：{exc}", file=sys.stderr)
            return 1
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"\n执行失败：{exc}", file=sys.stderr)
            return 1

        for block in messages[-1]["content"]:
            if block.type == "text":
                print(f"\n助手 > {block.text}")
        print()


if __name__ == "__main__":
    raise SystemExit(main())

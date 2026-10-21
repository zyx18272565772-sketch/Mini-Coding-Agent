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
            agent_loop(messages, query)
        except KeyboardInterrupt:
            print("\n任务已中断。")
            return 130
        except AnthropicError as exc:
            print(f"\n模型请求失败：{exc}", file=sys.stderr)
            return 1

        for block in messages[-1]["content"]:
            if block.type == "text":
                print(f"\n助手 > {block.text}")
        print()


if __name__ == "__main__":
    raise SystemExit(main())

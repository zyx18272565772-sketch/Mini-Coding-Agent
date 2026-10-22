"""按模型窗口计算上下文预算；参考 Kode-CLI 的默认预留和固定余量。"""

from dataclasses import dataclass
import json
from math import ceil
import os

from desktop_app.store import to_plain


def content_tokens(content) -> int:
    """与 Kode 一样做字符估算；不是 tokenizer 或计费用量。"""
    if not content:
        return 0
    if isinstance(content, str):
        return ceil(len(content) / 4)
    if isinstance(content, list):
        return sum(content_tokens(block) for block in content)
    if isinstance(content, dict):
        if content.get("type") == "text":
            return content_tokens(content.get("text", ""))
        if content.get("type") == "image":
            return 2000
        if content.get("type") == "tool_result":
            return content_tokens(content.get("content"))
    return ceil(len(json.dumps(content, ensure_ascii=False, default=str)) / 4)


def estimate_tokens(messages: list) -> int:
    return ceil(sum(content_tokens(message.get("content")) for message in to_plain(messages)) * 4 / 3)


@dataclass(frozen=True)
class ContextPolicy:
    context_tokens: int = 200000

    def __post_init__(self):
        if type(self.context_tokens) is not int or self.context_tokens <= 0:
            raise ValueError("MODEL_CONTEXT_TOKENS 必须是模型上下文窗口的正整数 Token 数")

    @classmethod
    def from_environment(cls):
        raw = os.getenv("MODEL_CONTEXT_TOKENS", "1000000")
        try:
            return cls(int(raw))
        except ValueError:
            raise ValueError("MODEL_CONTEXT_TOKENS 必须是模型上下文窗口的正整数 Token 数") from None

    @property
    def effective_limit(self):
        return max(1, self.context_tokens - min(20000, self.context_tokens // 10))

    @property
    def summary_threshold(self):
        return max(1, self.effective_limit - 13000)

    @property
    def micro_threshold(self):
        return max(0, self.summary_threshold - 20000)

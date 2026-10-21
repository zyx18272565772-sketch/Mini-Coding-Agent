"""钩子接口及默认注册；包首次导入时注册一次。"""

from hook.registry import HOOKS, register_hook, trigger_hooks
from hook.callbacks import (
    context_inject_hook,
    large_output_hook,
    log_hook,
    permission_hook,
    summary_hook,
)


register_hook("UserPromptSubmit", context_inject_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PreToolUse", log_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", summary_hook)

"""验证钩子注册、回调顺序及默认回调。"""

import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import hook
from hook import callbacks


class HookRegistryTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(hook.HOOKS, {event: [] for event in hook.HOOKS}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_callbacks_receive_arguments_in_registration_order(self):
        received = []
        hook.register_hook("PostToolUse", lambda block, output: received.append(("first", block, output)))
        hook.register_hook("PostToolUse", lambda block, output: received.append(("second", block, output)))

        self.assertIsNone(hook.trigger_hooks("PostToolUse", "tool call", "result"))
        self.assertEqual(received, [("first", "tool call", "result"), ("second", "tool call", "result")])

    def test_non_none_result_short_circuits_later_callbacks(self):
        later = Mock()
        hook.register_hook("PreToolUse", lambda block: "Blocked by hook")
        hook.register_hook("PreToolUse", later)

        self.assertEqual(hook.trigger_hooks("PreToolUse", "tool call"), "Blocked by hook")
        later.assert_not_called()

    def test_callbacks_only_run_for_their_registered_event(self):
        callback = Mock(return_value=None)
        hook.register_hook("PreToolUse", callback)
        self.assertIsNone(hook.trigger_hooks("PostToolUse", "tool call", "result"))
        callback.assert_not_called()

    def test_hook_failure_propagates_without_running_later_callbacks(self):
        later = Mock()
        hook.register_hook("PreToolUse", Mock(side_effect=RuntimeError("hook failed")))
        hook.register_hook("PreToolUse", later)
        with self.assertRaisesRegex(RuntimeError, "hook failed"):
            hook.trigger_hooks("PreToolUse", "tool call")
        later.assert_not_called()


class DefaultHookTests(unittest.TestCase):
    def test_permission_check_is_the_first_default_pre_tool_hook(self):
        self.assertIs(hook.HOOKS["PreToolUse"][0], callbacks.permission_hook)

    def test_permission_hook_converts_existing_permission_decision(self):
        block = SimpleNamespace(name="bash", input={"command": "echo test"})
        with patch("agent.permission.check_permission", return_value=True):
            self.assertIsNone(callbacks.permission_hook(block))
        with patch("agent.permission.check_permission", return_value=False):
            self.assertEqual(callbacks.permission_hook(block), "权限被拒绝。")

    def test_large_output_hook_warns_without_replacing_output(self):
        block = SimpleNamespace(name="read_file")
        with patch("builtins.print") as output:
            self.assertIsNone(callbacks.large_output_hook(block, "small"))
            output.assert_not_called()
            self.assertIsNone(callbacks.large_output_hook(block, "x" * 100001))
            output.assert_called_once()
            self.assertIn("read_file", output.call_args.args[0])

    def test_summary_counts_tool_results_including_denials(self):
        messages = [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "first"}]},
            {"role": "user", "content": [
                {"type": "tool_result", "content": "output"},
                {"type": "tool_result", "content": "权限被拒绝。"},
            ]},
        ]
        output = io.StringIO()
        with patch("sys.stdout", output):
            self.assertIsNone(callbacks.summary_hook(messages))
        self.assertIn("当前历史保留 2 条工具结果", output.getvalue())


if __name__ == "__main__":
    unittest.main()

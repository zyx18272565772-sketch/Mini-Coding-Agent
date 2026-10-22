"""验证工具产生实际影响之前的权限判断。"""

import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent import permission
from tool import workspace


def tool_call(name, **arguments):
    return SimpleNamespace(name=name, input=arguments)


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.workdir = self.root / "workspace"
        self.workdir.mkdir()
        self.patcher = patch.object(workspace, "WORKDIR", self.workdir)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.output = io.StringIO()
        stdout = patch("sys.stdout", self.output)
        stdout.start()
        self.addCleanup(stdout.stop)

    def test_hard_deny_takes_priority_and_never_asks_for_approval(self):
        for command in ("rm -rf /", "sudo echo test", "SHUTDOWN /s", "mkfs /dev/test", "dd if=test"):
            with self.subTest(command=command), patch("builtins.input") as prompt:
                self.assertFalse(permission.check_permission(tool_call("bash", command=command)))
                prompt.assert_not_called()

    def test_normal_workspace_operations_are_allowed_without_prompting(self):
        calls = [
            tool_call("read_file", path="demo.txt"),
            tool_call("search_files", query="demo"),
            tool_call("write_file", path="nested/demo.txt", content="hello"),
            tool_call("edit_file", path="demo.txt", old_text="old", new_text="new"),
            tool_call("glob", pattern="**/*.py"),
            tool_call("bash", command="dir"),
        ]
        with patch("builtins.input") as prompt:
            for call in calls:
                with self.subTest(call=call):
                    self.assertTrue(permission.check_permission(call))
            prompt.assert_not_called()

    def test_destructive_command_matching_is_case_insensitive_and_word_based(self):
        for command in ("del demo.txt", "DEL demo.txt", "rm demo.txt", "echo ok & DEL demo.txt"):
            with self.subTest(command=command):
                self.assertTrue(permission.contains_destructive_command(command))
        for command in ("model", "delimiter", "echo del demo.txt"):
            with self.subTest(command=command):
                self.assertFalse(permission.contains_destructive_command(command))

    def test_destructive_command_requires_explicit_user_approval(self):
        for choice, expected in (("y", True), (" YES ", True), ("", False), ("n", False), ("ok", False)):
            with self.subTest(choice=choice), patch("builtins.input", return_value=choice) as prompt:
                self.assertEqual(permission.check_permission(tool_call("bash", command="del demo.txt")), expected)
                prompt.assert_called_once()

    def test_relative_and_absolute_outside_paths_require_approval_for_all_file_tools(self):
        for path in ("../outside.txt", str(self.root / "outside.txt")):
            for name in ("read_file", "search_files", "write_file", "edit_file"):
                with self.subTest(path=path, tool=name):
                    with patch("builtins.input", return_value="n") as prompt:
                        self.assertFalse(permission.check_permission(tool_call(name, path=path)))
                        prompt.assert_called_once()
                    with patch("builtins.input", return_value="y"):
                        self.assertTrue(permission.check_permission(tool_call(name, path=path)))

    def test_approval_is_requested_again_for_each_call(self):
        call = tool_call("read_file", path="../outside.txt")
        with patch("builtins.input", side_effect=["y", "n"]) as prompt:
            self.assertTrue(permission.check_permission(call))
            self.assertFalse(permission.check_permission(call))
            self.assertEqual(prompt.call_count, 2)

    def test_model_supplied_approval_does_not_replace_user_input(self):
        call = tool_call("read_file", path="../outside.txt", approved=True)
        with patch("builtins.input", return_value="n") as prompt:
            self.assertFalse(permission.check_permission(call))
            prompt.assert_called_once()

    def test_end_of_input_defaults_to_denial(self):
        with patch("builtins.input", side_effect=EOFError):
            self.assertFalse(permission.check_permission(tool_call("bash", command="del demo.txt")))

    def test_keyboard_interrupt_propagates_without_approval(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                permission.check_permission(tool_call("bash", command="del demo.txt"))

    def test_malformed_tool_input_is_denied_without_asking(self):
        calls = [
            SimpleNamespace(name="bash", input="not an object"),
            tool_call("bash", command=None),
            tool_call("read_file", path=None),
            tool_call("read_file"),
        ]
        with patch("builtins.input") as prompt:
            for call in calls:
                with self.subTest(call=call):
                    self.assertFalse(permission.check_permission(call))
            prompt.assert_not_called()

    def test_approval_prompt_displays_tool_arguments_and_reason(self):
        with patch("builtins.input", return_value="n"):
            permission.check_permission(tool_call("bash", command="del demo.txt"))
        output = self.output.getvalue()
        self.assertIn("bash", output)
        self.assertIn("del demo.txt", output)


if __name__ == "__main__":
    unittest.main()

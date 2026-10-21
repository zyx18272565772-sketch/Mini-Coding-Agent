"""在不请求真实模型的情况下验证交互式入口。"""

import contextlib
import io
import os
import unittest
from unittest.mock import patch

from anthropic import APIConnectionError
from anthropic.types import TextBlock
import httpx2

import hook

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), \
        patch("dotenv.load_dotenv"):
    from agent import agentloop

import main


class MainTests(unittest.TestCase):
    def test_tasks_have_no_quota_options_or_quota_prompts(self):
        requests = []
        def reply(messages, active_request, **options):
            requests.append((list(messages), active_request, options))
            messages.append({"role": "assistant", "content": [TextBlock(type="text", text="完成")]})
        output = io.StringIO()
        with patch.object(agentloop, "agent_loop", side_effect=reply), \
                patch("builtins.input", side_effect=["任务", "exit"]), \
                contextlib.redirect_stdout(output):
            self.assertEqual(main.main(), 0)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][2], {})
        self.assertNotIn("额度", output.getvalue())

    def test_prompt_hook_runs_before_agent_for_each_nonempty_task(self):
        events = []

        def on_prompt(query):
            events.append(("input", query))
            return "ignored return value"

        def reply(messages, active_request):
            self.assertEqual(active_request, messages[-1]["content"])
            events.append(("agent", messages[-1]["content"]))
            messages.append({"role": "assistant", "content": [TextBlock(type="text", text="完成")]})

        with patch.dict(hook.HOOKS, {"UserPromptSubmit": [on_prompt]}), \
                patch.object(agentloop, "agent_loop", side_effect=reply), \
                patch("builtins.input", side_effect=["", "任务一", "任务二", "exit"]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main.main(), 0)
        self.assertEqual(events, [("input", "任务一"), ("agent", "任务一"),
                                  ("input", "任务二"), ("agent", "任务二")])

    def test_multiple_tasks_reuse_history_and_print_final_answers(self):
        received = []

        def reply(messages, active_request):
            self.assertEqual(active_request, messages[-1]["content"])
            received.append(list(messages))
            messages.append({"role": "assistant", "content": [TextBlock(type="text", text="完成")]})

        output = io.StringIO()
        with patch.object(agentloop, "agent_loop", side_effect=reply), \
                patch("builtins.input", side_effect=["", "  任务一  ", "任务二", "exit"]), \
                contextlib.redirect_stdout(output):
            code = main.main()

        self.assertEqual(code, 0)
        self.assertEqual(len(received), 2)
        self.assertEqual(received[0], [{"role": "user", "content": "任务一"}])
        self.assertEqual([message["role"] for message in received[1]], ["user", "assistant", "user"])
        self.assertEqual(output.getvalue().count("助手 > 完成"), 2)

    def test_exit_commands_and_end_of_input_do_not_call_agent(self):
        for query in ["q", "QUIT", " exit ", EOFError, KeyboardInterrupt]:
            with self.subTest(query=query), \
                    patch.object(agentloop, "agent_loop") as loop, \
                    patch("builtins.input", side_effect=[query]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main.main(), 0)
                loop.assert_not_called()

    def test_interrupting_a_task_ends_the_session(self):
        with patch.object(agentloop, "agent_loop", side_effect=KeyboardInterrupt), \
                patch("builtins.input", return_value="执行任务"), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main.main(), 130)

    def test_model_failure_is_reported_and_ends_the_session(self):
        error = APIConnectionError(request=httpx2.Request("POST", "https://api.example.test"))
        output = io.StringIO()
        with patch.object(agentloop, "agent_loop", side_effect=error), \
                patch("builtins.input", return_value="执行任务"), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(output):
            self.assertEqual(main.main(), 1)
        self.assertIn("模型请求失败", output.getvalue())


if __name__ == "__main__":
    unittest.main()

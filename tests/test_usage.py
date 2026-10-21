"""模型用量只统计、不限额，不访问网络。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llm.usage import UsageRun, run_usage, current_run
from llm.generation import create_message
from tests.streaming_support import model_response, message_events, encode_event


class UsageTests(unittest.TestCase):
    def test_all_purposes_share_counts_without_a_request_limit(self):
        run = UsageRun()
        for index in range(100):
            purpose = ("main", "subagent", "memory", "compact")[index % 4]
            run.begin(purpose)
            run.finish(purpose, {"input_tokens": 10, "output_tokens": 2})
        self.assertEqual(run.snapshot()["calls"], 100)
        self.assertEqual(run.snapshot()["total_tokens"], 1200)
        self.assertNotIn("limits", run.snapshot())

    def test_cache_tokens_and_missing_usage_are_distinguished(self):
        run = UsageRun()
        run.begin("main")
        run.finish("main", SimpleNamespace(input_tokens=10, output_tokens=3,
                   cache_read_input_tokens=20, cache_creation_input_tokens=2))
        run.begin("compact")
        run.finish("compact", None)
        snapshot = run.snapshot()
        self.assertEqual(snapshot["total_tokens"], 35)
        self.assertEqual(snapshot["unknown_calls"], 1)
        self.assertEqual(snapshot["by_purpose"]["compact"]["unknown_calls"], 1)

    def test_nested_scope_shares_budget_and_new_scope_resets(self):
        with run_usage() as parent:
            with run_usage() as child:
                self.assertIs(parent, child)
                child.begin("subagent")
                child.finish("subagent", {"input_tokens": 2, "output_tokens": 1})
            parent.begin("main")
            parent.finish("main", {"input_tokens": 2, "output_tokens": 1})
            self.assertEqual(parent.snapshot()["calls"], 2)
        self.assertIsNone(current_run())
        with run_usage() as fresh:
            self.assertEqual(fresh.snapshot()["calls"], 0)

    def test_old_token_threshold_no_longer_stops_requests_or_warns(self):
        run = UsageRun(previous={"run_id": "old-run", "limits": {"max_calls": 1, "max_tokens": 100}, "warning": True})
        run.begin("main")
        run.finish("main", {"input_tokens": 700_000, "output_tokens": 100_000})
        run.begin("main")
        run.finish("main", {"input_tokens": 300_000, "output_tokens": 100_000})
        run.begin("main")
        run.finish("main", {"input_tokens": 1, "output_tokens": 1})
        self.assertEqual(run.snapshot()["total_tokens"], 1_200_002)
        self.assertNotIn("limits", run.snapshot())
        self.assertNotIn("warning", run.snapshot())

    def test_old_token_environment_setting_is_ignored(self):
        with patch.dict("os.environ", {"AGENT_MAX_TOTAL_TOKENS": "invalid-old-setting", "AGENT_MAX_MODEL_CALLS": "invalid-old-setting"}):
            run = UsageRun()
        self.assertNotIn("limits", run.snapshot())

    def test_usage_reporting_never_prints_threshold_warnings(self):
        from agent.events import usage_reporter
        with patch("agent.events.emit") as emit, patch("builtins.print") as output:
            usage_reporter()({"warning": True, "total_tokens": 900000})
        emit.assert_called_once_with("usage", usage={"warning": True, "total_tokens": 900000})
        output.assert_not_called()

    def test_resume_preserves_counts_and_run_id(self):
        first = UsageRun()
        first.begin("main")
        first.finish("main", {"input_tokens": 4, "output_tokens": 2})
        resumed = UsageRun(previous=first.snapshot())
        resumed.begin("memory")
        resumed.finish("memory", {"input_tokens": 3, "output_tokens": 1})
        self.assertEqual(resumed.snapshot()["run_id"], first.snapshot()["run_id"])
        self.assertEqual(resumed.snapshot()["calls"], 2)
        self.assertEqual(resumed.snapshot()["total_tokens"], 10)

    def test_generation_counts_errors_and_later_requests_still_work(self):
        client = Mock()
        client.messages.create.side_effect = [RuntimeError("network"), SimpleNamespace(usage={"input_tokens": 3, "output_tokens": 1})]
        with run_usage() as run:
            with self.assertRaisesRegex(RuntimeError, "network"):
                create_message(client, purpose="main")
            create_message(client, purpose="memory")
            self.assertEqual(run.snapshot()["unknown_calls"], 1)
            self.assertEqual(run.snapshot()["calls"], 2)
        self.assertEqual(client.messages.create.call_count, 2)

    def test_streaming_counts_once_and_interruption_marks_usage_incomplete(self):
        from anthropic import Anthropic
        import httpx2
        for complete in (True, False):
            with self.subTest(complete=complete):
                content = [{"type": "text", "text": "结果"}]
                def respond(request):
                    if complete:
                        return model_response(content, stream=True)
                    body = b"".join(encode_event(e) for e in message_events(content, complete=False))
                    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)
                with Anthropic(api_key="test-key", max_retries=0, base_url="https://example.test",
                               http_client=httpx2.Client(transport=httpx2.MockTransport(respond))) as client:
                    with run_usage() as run:
                        try:
                            create_message(client, model="test-model", max_tokens=100,
                                           messages=[{"role": "user", "content": "测试"}], on_event=lambda *a, **k: None)
                        except RuntimeError:
                            self.assertFalse(complete)
                    snapshot = run.snapshot()
                    self.assertEqual(snapshot["calls"], 1)
                    self.assertEqual(snapshot["input_tokens"], 1)
                    self.assertEqual(snapshot["unknown_calls"], 0 if complete else 1)
                    self.assertEqual(snapshot["pending_calls"], 0)


if __name__ == "__main__":
    unittest.main()

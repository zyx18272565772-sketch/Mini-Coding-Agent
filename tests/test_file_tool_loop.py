"""通过真实 SDK 序列化验证主、子循环的搜索、分段读取和编辑纠错。"""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from anthropic import Anthropic
import httpx2

from tool import workspace
from tests.memory_support import isolate_memory

with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "MODEL_ID": "test-model"}), \
        patch("dotenv.load_dotenv"):
    from agent import agentloop as parent
    from agent import subagent


class FileToolLoopTests(unittest.TestCase):
    def setUp(self):
        isolate_memory(self, parent)

    def test_search_read_and_edit_retry_work_in_both_agent_loops(self):
        original = "def first():\n    return None\n\ndef second():\n    return None\n"
        updated = original.replace("def second():\n    return None", "def second():\n    return 42")
        for module in (parent, subagent):
            with self.subTest(agent=module.__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                target = root / "app.py"
                target.write_text(original, encoding="utf-8")
                requests = []

                def handle(request):
                    body = json.loads(request.content)
                    requests.append(body)
                    step = len(requests)
                    definitions = {item["name"]: item for item in body["tools"]}
                    self.assertIn("search_files", definitions)
                    self.assertIn("start_line", definitions["read_file"]["input_schema"]["properties"])
                    self.assertIn("replace_all", definitions["edit_file"]["input_schema"]["properties"])
                    if step == 1:
                        name, arguments = "search_files", {"query": "def second", "file_glob": "**/*.py"}
                    elif step == 2:
                        self.assertIn("app.py:4: def second", body["messages"][-1]["content"][0]["content"])
                        name, arguments = "read_file", {"path": "app.py", "start_line": 4, "end_line": 5}
                    elif step == 3:
                        self.assertEqual(body["messages"][-1]["content"][0]["content"],
                                         "4 | def second():\n5 |     return None")
                        name, arguments = "edit_file", {"path": "app.py", "old_text": "return None", "new_text": "return 42"}
                    elif step == 4:
                        self.assertIn("上下文", body["messages"][-1]["content"][0]["content"])
                        self.assertEqual(target.read_text(encoding="utf-8"), original)
                        name, arguments = "edit_file", {"path": "app.py", "old_text": "def second():\n    return None",
                                                        "new_text": "def second():\n    return 42"}
                    else:
                        self.assertEqual(step, 5)
                        self.assertIn("已修改", body["messages"][-1]["content"][0]["content"])
                        self.assertEqual(target.read_text(encoding="utf-8"), updated)
                    content = [{"type": "tool_use", "id": f"call_{step}", "name": name, "input": arguments}] \
                        if step < 5 else [{"type": "text", "text": "已完成准确修改"}]
                    return httpx2.Response(200, json={
                        "id": f"msg_{step}", "type": "message", "role": "assistant", "model": "test-model",
                        "content": content, "stop_reason": "tool_use" if step < 5 else "end_turn",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    })

                with patch.object(workspace, "WORKDIR", root), Anthropic(
                    api_key="test-key", base_url="https://api.example.test", max_retries=0,
                    http_client=httpx2.Client(transport=httpx2.MockTransport(handle)),
                ) as client, patch.object(module, "client", client), patch("sys.stdout", io.StringIO()):
                    if module is parent:
                        messages = [{"role": "user", "content": "只修改 second 函数"}]
                        parent.agent_loop(messages, messages[-1]["content"])
                    else:
                        self.assertEqual(subagent.run_subagent("只修改 second 函数"), "已完成准确修改")
                self.assertEqual(len(requests), 5)


if __name__ == "__main__":
    unittest.main()

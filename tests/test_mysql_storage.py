"""真实 MySQL 集成测试。MYSQL_TEST_CONFIG 指向隔离实例的配置文件。"""
import json
import os
from pathlib import Path
import tempfile
import unittest
import time
from uuid import uuid4
from unittest.mock import patch


def mysql_worker(connection, payload):
    """真实工作进程、模拟模型，验证父子进程共享身份而不共享连接。"""
    from tool import workspace
    from tool.task_system import configure_owner, run_create_task
    from storage.context import storage_scope
    from storage.factory import memory_store
    from context.compactor import ContextCompactor
    from llm.usage import UsageRun
    workspace.WORKDIR = Path(payload["workdir"])
    configure_owner(payload["session_id"])
    with storage_scope(session_id=payload["session_id"], run_id=payload["run_id"]):
        run_create_task("运行测试")
        memory_store(workspace.WORKDIR).add([dict(name="集成偏好", type="user", description="中文说明", body="使用中文说明实现", scope="persistent")])
        ContextCompactor(None, "mock", workspace.WORKDIR / ".transcripts", workspace.WORKDIR / ".task_outputs/tool-results").save_output("call_1", "测试通过")
        usage = UsageRun(previous=payload.get("previous_usage"), run_id=payload["run_id"])
        usage.begin("main")
        usage.finish("main", {"input_tokens": 7, "output_tokens": 2})
        failing = payload["active_request"] == "重试测试" and not payload.get("previous_usage")
        usage.status = "error" if failing else "completed"
        connection.send({"type": "usage", "usage": usage.snapshot()})
        connection.send({"type": "assistant", "text": "已完成"})
        connection.send({"type": "checkpoint", "messages": payload["messages"] + [{"role": "assistant", "content": "已完成"}]})
    connection.send({"type": "failure", "kind": "connection", "message": "模拟断线", "retryable": True} if failing else {"type": "done"})
    connection.close()


@unittest.skipUnless(os.environ.get("MYSQL_TEST_CONFIG"), "需要独立 MySQL 测试实例")
class MySQLStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from storage.database import get_database
        from storage.manage import upgrade
        cls.environment = dict(os.environ)
        os.environ.update(json.loads(Path(os.environ["MYSQL_TEST_CONFIG"]).read_text(encoding="utf-8")))
        cls.db = get_database()
        upgrade(cls.db)
        cls.db.verify()

    @classmethod
    def tearDownClass(cls):
        cls.db.engine.dispose()
        os.environ.clear()
        os.environ.update(cls.environment)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workdir = Path(self.temp.name)
        from storage.sessions import SQLSessionStore
        self.store = SQLSessionStore(self.db)
        self.session = self.store.create(self.workdir)

    def tearDown(self):
        from sqlalchemy import delete
        from storage.models import projects
        from storage.database import path_key
        with self.db.transaction() as connection:
            connection.execute(delete(projects).where(projects.c.path_key == path_key(self.workdir)))
        self.temp.cleanup()

    def test_context_history_usage_and_optimistic_lock(self):
        from sqlalchemy import select
        from storage.models import chat_messages, context_checkpoints, run_usage
        from storage.database import StorageError
        sid = self.session["id"]
        rid = uuid4().hex
        self.session.update(run_id=rid, active_request="写测试", status="completed",
                            messages=[{"role": "user", "content": "压缩后的上下文"}])
        self.session["events"] = [
            {"seq": 1, "type": "user", "text": "原始指令", "run_id": rid},
            {"seq": 2, "type": "assistant_start", "message_id": "a"},
            {"seq": 3, "type": "assistant_delta", "message_id": "a", "text": "你"},
            {"seq": 4, "type": "assistant_delta", "message_id": "a", "text": "好"},
            {"seq": 5, "type": "assistant", "message_id": "a", "text": "你好"},
        ]
        self.session["usage_runs"] = {rid: {"run_id": rid, "by_purpose": {"main": {"calls": 2, "input_tokens": 21, "output_tokens": 3}}}}
        self.store.save(self.session)
        loaded = next(s for s in self.store.load_all() if s["id"] == sid)
        self.assertEqual(loaded["messages"], self.session["messages"])
        self.assertEqual(loaded["events"], self.session["events"])
        self.assertEqual(loaded["usage"]["total_tokens"], 24)
        with self.db.transaction() as c:
            bodies = c.execute(select(chat_messages.c.body).where(chat_messages.c.session_id == sid).order_by(chat_messages.c.seq)).scalars().all()
            self.assertEqual(bodies, ["原始指令", "你好"])
            before = len(c.execute(select(context_checkpoints).where(context_checkpoints.c.session_id == sid)).all())
        self.store.save(self.session)
        with self.db.transaction() as c:
            self.assertEqual(len(c.execute(select(context_checkpoints).where(context_checkpoints.c.session_id == sid)).all()), before)
            self.assertEqual(c.execute(select(run_usage.c.calls).where(run_usage.c.run_id == rid)).scalars().all(), [2])
        with self.assertRaises(StorageError):
            self.store.save(loaded)

    def test_task_dependencies_concurrent_claim_and_project_isolation(self):
        from concurrent.futures import ThreadPoolExecutor
        from storage.tasks import SQLTaskStore
        store = SQLTaskStore(self.workdir, self.db)
        a, b = store.create("实现接口"), store.create("验证接口")
        store.update_dependencies(b.id, [a.id])
        with self.assertRaises(ValueError):
            store.claim(b.id, self.session["id"])
        with self.assertRaises(ValueError):
            store.update_dependencies(a.id, [b.id])
        def claim(owner):
            try:
                SQLTaskStore(self.workdir, self.db).claim(a.id, owner)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(pool.map(claim, ["one", "two"])), 1)
        claimed = store.load(a.id)
        store.complete(a.id, claimed.owner)
        store.claim(b.id, self.session["id"])
        self.store.delete(self.session["id"])
        self.assertEqual(store.load(b.id).status, "pending")
        self.assertFalse((self.workdir / ".tasks").exists())

    def test_memory_compare_snapshot_and_source(self):
        from storage.memories import SQLMemoryStore
        from storage.context import storage_scope
        store = SQLMemoryStore(self.workdir, self.db)
        record = dict(name="回复偏好", type="user", description="用中文沟通", body="始终使用中文解释代码", scope="persistent")
        with storage_scope(session_id=self.session["id"]):
            self.assertEqual(store.add([record]), 1)
        self.assertEqual(store.add([record]), 0)
        snapshot = store.snapshot()
        self.assertIn("回复偏好.md", snapshot)
        self.assertFalse(store.replace([record], {}))
        self.assertTrue(store.replace([record], snapshot))
        self.assertFalse((self.workdir / ".memory").exists())

    def test_desktop_spawn_and_reload(self):
        from desktop_app.service import DesktopService
        from sqlalchemy import select
        from storage.models import artifacts, memories
        service = DesktopService(self.workdir, self.workdir / "ui", worker_target=mysql_worker)
        try:
            view = service.create_session()["data"]
            sid = view["id"]
            self.assertNotIn("_db_version", view)
            reply = service.send_message(sid, "实现并测试")
            self.assertTrue(reply["ok"], reply)
            deadline = time.monotonic() + 25
            while sid in service._jobs and time.monotonic() < deadline:
                time.sleep(.05)
            view = service.get_session(sid)["data"]
            self.assertEqual(view["status"], "completed", view.get("failure"))
            self.assertEqual(view["usage"]["total_tokens"], 9)
            self.assertEqual(len(view["tasks"]), 1)
            restored = DesktopService(self.workdir, self.workdir / "ui", worker_target=mysql_worker)
            loaded = restored.get_session(sid)["data"]
            self.assertEqual(loaded["usage"]["run_id"], view["usage"]["run_id"])
            self.assertEqual(loaded["events"], view["events"])
            with self.db.transaction() as c:
                self.assertEqual(c.execute(select(artifacts.c.run_id).where(artifacts.c.session_id == sid)).scalar(), loaded["run_id"])
                self.assertEqual(c.execute(select(memories.c.source_run_id).where(memories.c.source_session_id == sid)).scalar(), loaded["run_id"])
            self.assertFalse((self.workdir / "ui" / f"{sid}.json").exists())
        finally:
            service.shutdown()

    def test_import_is_idempotent_preserves_newer_data_and_rolls_back(self):
        from desktop_app.store import SessionStore
        from desktop_app.catalog import ProjectCatalog
        from task_system.store import TaskStore
        from memory.store import FileMemoryStore
        from storage.migrate import import_files
        from storage.tasks import SQLTaskStore
        from storage.memories import SQLMemoryStore
        source = self.workdir / "old_sessions"
        # 目标已有同路径项目，沿用它的 ID；迁移不改动源文件。
        pid = self.session["project_id"]
        catalog = ProjectCatalog(source / "workspace.json")
        project = catalog.for_directory(self.workdir)
        project["id"] = pid
        catalog.save()
        old = SessionStore(source).create(self.workdir)
        old.update(project_id=pid, title="原始会话", events=[dict(seq=1, type="user", text="旧原文")])
        SessionStore(source).save(old)
        task = TaskStore(self.workdir).create("旧任务")
        FileMemoryStore(self.workdir).add([dict(name="旧偏好", type="user", description="中文解释", body="解释时使用中文", scope="persistent")])
        original = (source / f"{old['id']}.json").read_bytes()
        report = import_files(self.db, source)
        self.assertEqual(report["sessions_imported"], 1)
        self.assertEqual(SQLTaskStore(self.workdir, self.db).load(task.id).subject, "旧任务")
        self.assertEqual(len(SQLMemoryStore(self.workdir, self.db).list_records()), 1)
        loaded = next(s for s in self.store.load_all() if s["id"] == old["id"])
        loaded["title"] = "数据库新标题"
        self.store.save(loaded)
        self.assertEqual(import_files(self.db, source)["sessions_imported"], 0)
        self.assertEqual(next(s for s in self.store.load_all() if s["id"] == old["id"])["title"], "数据库新标题")
        self.assertEqual((source / f"{old['id']}.json").read_bytes(), original)
        newer = SessionStore(source).create(self.workdir)
        # 无效依赖会使本次导入整体回滚，不能只导入一半。
        path = self.workdir / ".tasks" / f"{task.id}.json"
        broken = json.loads(path.read_text(encoding="utf-8"))
        broken["blockedBy"] = ["task_ffffffff"]
        path.write_text(json.dumps(broken), encoding="utf-8")
        with self.assertRaises(ValueError):
            import_files(self.db, source)
        self.assertNotIn(newer["id"], [s["id"] for s in self.store.load_all()])

    def test_transaction_failure_does_not_save_partial_events(self):
        from sqlalchemy import select, func
        from storage.models import session_events
        sid = self.session["id"]
        previous_version = self.session["_db_version"]
        self.session["events"] = [dict(seq=1, type="user", text="失败写入")]
        with patch("storage.sessions.save_chat", side_effect=RuntimeError("模拟写入失败")):
            with self.assertRaisesRegex(RuntimeError, "模拟写入失败"):
                self.store.save(self.session)
        self.assertEqual(self.session["_db_version"], previous_version)
        with self.db.transaction() as c:
            self.assertEqual(c.execute(select(func.count()).select_from(session_events).where(session_events.c.session_id == sid)).scalar(), 0)
        self.store.save(self.session)
        self.assertEqual(next(s for s in self.store.load_all() if s["id"] == sid)["events"], self.session["events"])

    def test_retry_after_restart_reuses_run_and_usage(self):
        from desktop_app.service import DesktopService
        from sqlalchemy import select
        from storage.models import agent_runs
        service = DesktopService(self.workdir, self.workdir / "ui", worker_target=mysql_worker)
        sid = service.create_session()["data"]["id"]
        def wait(instance):
            deadline = time.monotonic() + 20
            while sid in instance._jobs and time.monotonic() < deadline:
                time.sleep(.05)
            return instance.get_session(sid)["data"]
        try:
            self.assertTrue(service.send_message(sid, "重试测试")["ok"])
            first = wait(service)
            self.assertEqual(first["status"], "error")
            restored = DesktopService(self.workdir, self.workdir / "ui", worker_target=mysql_worker)
            try:
                self.assertTrue(restored.retry_run(sid)["ok"])
                second = wait(restored)
                self.assertEqual(second["status"], "completed", second.get("failure"))
                self.assertEqual(second["usage"]["run_id"], first["usage"]["run_id"])
                self.assertEqual(second["usage"]["calls"], 2)
                self.assertEqual(sum(e["type"] == "user" for e in second["events"]), 1)
                with self.db.transaction() as c:
                    self.assertEqual(c.execute(select(agent_runs.c.attempt).where(agent_runs.c.session_id == sid)).scalars().all(), [2])
            finally:
                restored.shutdown()
        finally:
            service.shutdown()

    def test_cli_records_only_safe_checkpoints(self):
        from storage.cli import CLIHistory
        from agent.events import emit
        from llm.usage import run_usage
        history = CLIHistory(self.db, self.workdir)
        messages = [dict(role="user", content="命令行任务")]
        with history.run(messages, "命令行任务") as rid:
            with run_usage(run_id=rid, report=lambda u: emit("usage", usage=u)) as usage:
                usage.begin("main")
                usage.finish("main", {"input_tokens": 2, "output_tokens": 1})
                messages.append(dict(role="assistant", content="命令行回复"))
                emit("assistant", text="命令行回复")
                emit("checkpoint", messages=messages)
                messages.append(dict(role="assistant", content=[dict(type="tool_use", id="partial", name="bash", input={})]))
        loaded = next(s for s in self.store.load_all() if s["id"] == history.session["id"])
        self.assertEqual(len(loaded["messages"]), 2)
        self.assertEqual(loaded["status"], "completed")
        self.assertEqual(loaded["usage"]["total_tokens"], 3)

    def test_project_removal_is_atomic_and_preserves_project_knowledge(self):
        from desktop_app.service import DesktopService
        from storage.tasks import SQLTaskStore
        service = DesktopService(self.workdir, self.workdir / "ui")
        pid = service.bootstrap()["data"]["projects"][0]["id"]
        sid = service.create_session(pid)["data"]["id"]
        tasks = SQLTaskStore(self.workdir, self.db)
        task = tasks.create("保留任务")
        tasks.claim(task.id, sid)
        self.assertTrue(service.delete_project(pid)["ok"])
        self.assertEqual(service.bootstrap()["data"]["projects"], [])
        restored = DesktopService(self.workdir, self.workdir / "ui")
        self.assertEqual(restored.bootstrap()["data"]["projects"], [])
        self.assertEqual(tasks.load(task.id).status, "pending")
        self.assertEqual(restored._catalog.for_directory(self.workdir)["id"], pid)

    def test_send_storage_failure_keeps_ui_state_consistent(self):
        from desktop_app.service import DesktopService
        from storage.database import StorageError
        service = DesktopService(self.workdir, self.workdir / "ui")
        sid = service.create_session()["data"]["id"]
        with patch.object(service._store, "save", side_effect=StorageError("模拟数据库断开")):
            reply = service.send_message(sid, "不能保存的任务")
        self.assertFalse(reply["ok"])
        view = service.get_session(sid)["data"]
        self.assertEqual(view["status"], "idle")
        self.assertEqual(view["events"], [])
        self.assertEqual(service._sessions[sid]["messages"], [])

    def test_real_sdk_loop_writes_restores_and_resumes(self):
        from desktop_app.service import DesktopService
        from tests.test_desktop_retry import retry_worker
        service = DesktopService(self.workdir, self.workdir / "ui", worker_target=retry_worker)
        sid = service.create_session()["data"]["id"]
        def wait(instance):
            deadline = time.monotonic() + 25
            while sid in instance._jobs and time.monotonic() < deadline:
                time.sleep(.05)
            return instance.get_session(sid)["data"]
        with patch.dict(os.environ, {"GITHUB_MCP_ENABLED": "false"}):
            try:
                self.assertTrue(service.send_message(sid, "失败后继续验收")["ok"])
                failed = wait(service)
                self.assertEqual(failed["failure"]["kind"], "connection", failed.get("failure"))
                self.assertEqual((self.workdir / "progress.txt").read_text(encoding="utf-8"), "第一次写入")
                resumed = DesktopService(self.workdir, self.workdir / "ui", worker_target=retry_worker)
                try:
                    self.assertTrue(resumed.retry_run(sid)["ok"])
                    completed = wait(resumed)
                    self.assertEqual(completed["status"], "completed", completed.get("failure"))
                    self.assertEqual(completed["usage"]["run_id"], failed["usage"]["run_id"])
                    writes = [e for e in completed["events"] if e["type"] == "tool_start" and e.get("name") == "write_file"]
                    self.assertEqual(len(writes), 1)
                finally:
                    resumed.shutdown()
            finally:
                service.shutdown()

    def test_schema_and_timestamp_precision(self):
        from sqlalchemy import inspect
        from storage.models import metadata
        from storage.database import timestamp
        inspector = inspect(self.db.engine)
        self.assertEqual(set(inspector.get_table_names()), {*metadata.tables, "alembic_version"})
        for name, table in metadata.tables.items():
            self.assertEqual({c["name"] for c in inspector.get_columns(name)}, set(table.c.keys()))
        columns = {c["name"]: c for c in inspector.get_columns("sessions")}
        self.assertEqual(columns["updated_at"]["type"].fsp, 6)
        loaded = next(s for s in self.store.load_all() if s["id"] == self.session["id"])
        self.assertEqual(timestamp(loaded["created_at"]), timestamp(self.session["created_at"]))

    def test_existing_empty_catalog_does_not_recreate_default_project(self):
        from desktop_app.service import DesktopService
        source = self.workdir / "ui"
        source.mkdir()
        (source / "workspace.json").write_text(json.dumps(dict(version=1, projects=[], settings=dict(theme="light"))))
        default = self.workdir / "new_default"
        default.mkdir()
        service = DesktopService(default, source)
        paths = [Path(p["workdir"]) for p in service.bootstrap()["data"]["projects"]]
        self.assertNotIn(default, paths)

    def test_archives_store_body_in_mysql_without_creating_directories(self):
        from tool import workspace
        self.enterContext(patch.object(workspace, "WORKDIR", self.workdir))
        from anthropic.types import ToolUseBlock
        from context.compactor import ContextCompactor
        from storage.context import storage_scope
        from storage.models import artifacts
        from sqlalchemy import select
        compactor = ContextCompactor(None, "mock", self.workdir / ".transcripts", self.workdir / ".task_outputs/tool-results")
        messages = [dict(role="assistant", content=[ToolUseBlock(type="tool_use", id="call_1", name="read_file", input={"path": "main.py"})])]
        with storage_scope(session_id=self.session["id"]):
            transcript = compactor.write_transcript(messages)
            output = compactor.save_output("call_1", "第一行\n第二行\n第三行\n")
        self.assertTrue(str(transcript).startswith("artifact://"))
        self.assertTrue(str(output).startswith("artifact://"))
        self.assertFalse((self.workdir / ".transcripts").exists())
        self.assertFalse((self.workdir / ".task_outputs").exists())
        with self.db.transaction() as c:
            rows = c.execute(select(artifacts).where(artifacts.c.session_id == self.session["id"])).mappings().all()
        self.assertEqual(len(rows), 2)
        archive = next(r for r in rows if r["kind"] == "transcript")
        self.assertEqual(json.loads(archive["content"])["content"][0]["input"], {"path": "main.py"})
        self.assertIsNone(archive["path"])
        from tool import workspace
        from tool.read_file import run_read
        from agent.permission import check_permission
        from types import SimpleNamespace
        with patch.object(workspace, "WORKDIR", self.workdir), patch("builtins.input") as ask:
            self.assertTrue(check_permission(SimpleNamespace(name="read_file", input={"path": str(output)})))
            ask.assert_not_called()
            self.assertEqual(run_read(str(output), start_line=2, end_line=3), "2 | 第二行\n3 | 第三行")
            self.assertEqual(run_read(str(output), start_line=4), "（起始行超过文件末尾）")

    def test_database_archive_compaction_reuses_references(self):
        from context.compactor import ContextCompactor
        from storage.models import artifacts
        from sqlalchemy import select, func
        from tool import workspace
        self.enterContext(patch.object(workspace, "WORKDIR", self.workdir))
        from tool.read_file import run_read
        compactor = ContextCompactor(None, "mock", self.workdir / ".transcripts", self.workdir / ".task_outputs/tool-results")
        original = "检索结果\n" * 10000
        preview = compactor.persisted_preview("read_1", original)
        reference = compactor.persisted_output_path(preview)
        self.assertTrue(reference.startswith("artifact://"))
        self.assertEqual(compactor.persisted_output_path(compactor.persisted_preview("read_2", preview)), reference)
        history = [dict(role="user", content=f"旧消息 {n}") for n in range(70)]
        with patch.object(compactor, "summarize_history", return_value="旧工作已整理"):
            trimmed = compactor.compact_history(history, "继续工作")
        self.assertIn("artifact://", trimmed[0]["content"])
        self.assertEqual(compactor.prepare(trimmed, "继续工作"), trimmed)
        with self.db.transaction() as c:
            pid = self.session["project_id"]
            self.assertEqual(c.execute(select(func.count()).select_from(artifacts).where(artifacts.c.project_id == pid)).scalar(), 2)
        # 新建读取对象仍能找回正文；分页输出不会重新塞回全部结果。
        with patch.object(workspace, "WORKDIR", self.workdir):
            read = run_read(reference, start_line=9999, limit=2)
        self.assertIn("9999 | 检索结果", read)
        self.assertNotIn("后续内容未读取", read)

    def test_archive_reads_enforce_project_scope_and_output_bounds(self):
        from context.compactor import ContextCompactor
        from tool import workspace
        self.enterContext(patch.object(workspace, "WORKDIR", self.workdir))
        from tool.read_file import run_read, MAX_OUTPUT_CHARS
        compactor = ContextCompactor(None, "mock", self.workdir / ".transcripts", self.workdir / ".task_outputs/tool-results")
        reference = str(compactor.save_output("wide", "私有结果" * 100000))
        with patch.object(workspace, "WORKDIR", self.workdir):
            output = run_read(reference)
            self.assertIn("行内内容已截断", output)
            self.assertLess(len(output), MAX_OUTPUT_CHARS + 200)
            self.assertTrue(run_read("artifact://invalid").startswith("错误："))
            self.assertTrue(run_read("artifact://" + "f" * 32).startswith("错误："))
        with patch.object(workspace, "WORKDIR", self.workdir / "another-project"):
            denied = run_read(reference)
            self.assertTrue(denied.startswith("错误："))
            self.assertNotIn("私有结果", denied)

    def test_microcompact_database_results_keep_previews_and_recent_three(self):
        from context.compactor import ContextCompactor
        from tool import workspace
        from tool.read_file import run_read
        history = [dict(role="user", content="阅读项目")]
        for n in range(10):
            history.extend([
                dict(role="assistant", content=[dict(type="tool_use", id=f"read_{n}", name="read_file", input={"path": "file.py"})]),
                dict(role="user", content=[dict(type="tool_result", tool_use_id=f"read_{n}", content=str(n) * 50000)]),
            ])
        compactor = ContextCompactor(None, "mock", self.workdir / ".transcripts", self.workdir / ".task_outputs/tool-results")
        prepared = compactor.prepare(history, "阅读项目")
        results = [message["content"][0] for message in prepared if compactor.is_tool_result(message)]
        for n, result in enumerate(results[:7]):
            reference = compactor.persisted_output_path(result["content"])
            self.assertTrue(reference.startswith("artifact://"))
            self.assertIn(str(n) * 400, result["content"])
            self.assertNotIn(str(n) * 401, result["content"])
            with patch.object(workspace, "WORKDIR", self.workdir):
                self.assertIn(str(n) * 1000, run_read(reference))
        self.assertEqual([result["content"] for result in results[7:]], [str(n) * 50000 for n in range(7, 10)])
        self.assertFalse((self.workdir / ".task_outputs").exists())
        self.assertFalse((self.workdir / ".transcripts").exists())

    def test_main_and_subagent_read_compacted_database_outputs(self):
        from tool import workspace
        from tests.test_compact_loop import parent, subagent, response, text_response, tool
        from tests.memory_support import isolate_memory
        from storage.context import storage_scope
        from unittest.mock import Mock
        import re
        isolate_memory(self, parent)
        original = "工具结果明细\n" * 60000
        with patch.object(workspace, "WORKDIR", self.workdir), patch.dict(os.environ, {"GITHUB_MCP_ENABLED": "false"}):
            for module, handlers in ((parent, parent.TOOL_HANDLERS), (subagent, subagent.SUB_HANDLERS)):
                requests = []
                def reply(**body):
                    requests.append(body)
                    if len(requests) == 1:
                        return response([tool("huge", "bash", command="fake-large-output")])
                    if len(requests) == 2:
                        output = body["messages"][-1]["content"][0]["content"]
                        reference = re.search(r"artifact://[0-9a-f]{32}", output).group()
                        return response([tool("recover", "read_file", path=reference, start_line=19999, limit=2)])
                    self.assertIn("19999 | 工具结果明细", body["messages"][-1]["content"][0]["content"])
                    return text_response("已找回归档，任务完成")
                client = Mock()
                client.messages.create.side_effect = reply
                with patch.object(module, "client", client), patch.dict(handlers, {"bash": lambda command: original}), \
                        storage_scope(session_id=self.session["id"]):
                    if module is parent:
                        module.agent_loop([dict(role="user", content="查阅明细")], "查阅明细")
                    else:
                        self.assertEqual(module.run_subagent("查阅明细"), "已找回归档，任务完成")
                self.assertEqual(len(requests), 3)
        self.assertFalse((self.workdir / ".transcripts").exists())
        self.assertFalse((self.workdir / ".task_outputs").exists())

    def test_database_archive_paging_matches_file_tool(self):
        from context.compactor import ContextCompactor
        from tool import workspace
        from tool.read_file import run_read
        compactor = ContextCompactor(None, "mock", self.workdir / ".transcripts", self.workdir / ".task_outputs/tool-results")
        samples = ("", "one", "一\r\n二\r\n三\r\n", "\ufeff开头\n\n末尾", "x\n" * 1005,
                   "超长行" * 50000 + "\n尾行\n", "binary\x00data")
        with patch.object(workspace, "WORKDIR", self.workdir):
            for index, text in enumerate(samples):
                path = self.workdir / f"plain_{index}.txt"
                path.write_bytes(text.encode("utf-8"))
                reference = str(compactor.save_output(str(index), text))
                for options in ({}, {"start_line": 2, "limit": 2}, {"start_line": 999, "end_line": 1005}):
                    with self.subTest(sample=index, options=options):
                        self.assertEqual(run_read(reference, **options), run_read(str(path), **options))

    def test_legacy_file_archive_remains_readable(self):
        from storage.artifacts import register_file
        from storage.models import artifacts
        from sqlalchemy import select
        from context.compactor import ContextCompactor
        from tool import workspace
        from tool.read_file import run_read
        old = self.workdir / ".task_outputs/tool-results/old.txt"
        old.parent.mkdir(parents=True)
        old.write_text("旧正文\n仍可读取\n", encoding="utf-8")
        register_file(self.db, self.workdir, old, "tool_result")
        compactor = ContextCompactor(None, "mock", self.workdir / ".transcripts", old.parent)
        marker = f"[早期工具结果已保存至 {old}]"
        self.assertEqual(compactor.persisted_output_path(marker), str(old))
        with self.db.transaction() as c:
            row = c.execute(select(artifacts).where(artifacts.c.project_id == self.session["project_id"])).mappings().one()
            self.assertIsNone(row["content"])
            self.assertEqual(row["path"], str(old))
        with patch.object(workspace, "WORKDIR", self.workdir):
            self.assertEqual(run_read(str(old)), "1 | 旧正文\n2 | 仍可读取")


if __name__ == "__main__":
    unittest.main()

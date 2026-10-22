"""十一张业务表；JSON 内容保留模型协议，常用查询字段使用独立列。"""

from sqlalchemy import (BigInteger, Boolean, Column, DateTime, ForeignKey, ForeignKeyConstraint,
                        Index, Integer, JSON, MetaData, String, Table, Text, UniqueConstraint)
from sqlalchemy.dialects.mysql import DATETIME, LONGTEXT


metadata = MetaData(naming_convention={"ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s", "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s"})
body_type = Text().with_variant(LONGTEXT(), "mysql")
timestamp_type = DateTime().with_variant(DATETIME(fsp=6), "mysql")


def table(name, *columns):
    return Table(name, metadata, *columns, mysql_engine="InnoDB", mysql_charset="utf8mb4",
                 mysql_collate="utf8mb4_unicode_ci")


projects = table("projects",
    Column("id", String(32), primary_key=True), Column("name", String(255), nullable=False),
    Column("workdir", Text, nullable=False), Column("path_key", String(64), nullable=False, unique=True),
    Column("created_at", timestamp_type, nullable=False), Column("archived", Boolean, nullable=False, default=False))

sessions = table("sessions",
    Column("id", String(32), primary_key=True),
    Column("project_id", String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    Column("title", String(255), nullable=False), Column("title_custom", Boolean, nullable=False, default=False),
    Column("workdir", Text, nullable=False), Column("status", String(24), nullable=False),
    Column("latest_checkpoint_id", String(32)), Column("current_run_id", String(32)),
    Column("context_hash", String(64)), Column("version", Integer, nullable=False, default=0),
    Column("created_at", timestamp_type, nullable=False), Column("updated_at", timestamp_type, nullable=False),
    Index("ix_sessions_project_updated", "project_id", "updated_at", "id"))

agent_runs = table("agent_runs",
    Column("id", String(32), primary_key=True),
    Column("session_id", String(32), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("request", body_type, nullable=False), Column("model", String(255), nullable=False),
    Column("workdir", Text, nullable=False), Column("status", String(24), nullable=False),
    Column("failure", JSON), Column("attempt", Integer, nullable=False, default=1),
    Column("started_at", timestamp_type, nullable=False), Column("ended_at", timestamp_type),
    Index("ix_agent_runs_session_started", "session_id", "started_at"))

context_checkpoints = table("context_checkpoints",
    Column("id", String(32), primary_key=True),
    Column("session_id", String(32), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("run_id", String(32), ForeignKey("agent_runs.id", ondelete="CASCADE")),
    Column("version", Integer, nullable=False), Column("messages_json", JSON, nullable=False),
    Column("schema_version", Integer, nullable=False, default=1), Column("created_at", timestamp_type, nullable=False),
    UniqueConstraint("session_id", "version"))

chat_messages = table("chat_messages",
    Column("id", String(32), primary_key=True),
    Column("session_id", String(32), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("run_id", String(32), ForeignKey("agent_runs.id", ondelete="SET NULL")),
    Column("message_key", String(255), nullable=False), Column("seq", BigInteger, nullable=False),
    Column("role", String(16), nullable=False), Column("scope", String(20), nullable=False),
    Column("body", body_type, nullable=False), Column("status", String(24), nullable=False),
    Column("created_at", timestamp_type, nullable=False), UniqueConstraint("session_id", "message_key"),
    Index("ix_chat_messages_session_seq", "session_id", "seq"))

session_events = table("session_events",
    Column("id", String(32), primary_key=True),
    Column("session_id", String(32), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
    Column("run_id", String(32), ForeignKey("agent_runs.id", ondelete="SET NULL")),
    Column("seq", BigInteger, nullable=False), Column("event_type", String(40), nullable=False),
    Column("payload", JSON, nullable=False), Column("created_at", timestamp_type, nullable=False),
    UniqueConstraint("session_id", "seq"))

run_usage = table("run_usage",
    Column("run_id", String(32), ForeignKey("agent_runs.id", ondelete="CASCADE"), primary_key=True),
    Column("purpose", String(20), primary_key=True),
    *[Column(name, BigInteger, nullable=False, default=0) for name in
      ("calls", "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "unknown_calls", "pending_calls")],
    Column("updated_at", timestamp_type, nullable=False))

project_tasks = table("project_tasks",
    Column("project_id", String(32), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    Column("task_id", String(32), primary_key=True), Column("subject", Text, nullable=False),
    Column("description", body_type, nullable=False), Column("status", String(24), nullable=False),
    Column("owner_session_id", String(64)), Column("updated_at", timestamp_type, nullable=False))

task_dependencies = table("task_dependencies",
    Column("project_id", String(32), primary_key=True), Column("task_id", String(32), primary_key=True),
    Column("depends_on", String(32), primary_key=True),
    ForeignKeyConstraint(["project_id", "task_id"], ["project_tasks.project_id", "project_tasks.task_id"], name="fk_dependencies_task", ondelete="CASCADE"),
    ForeignKeyConstraint(["project_id", "depends_on"], ["project_tasks.project_id", "project_tasks.task_id"], name="fk_dependencies_parent", ondelete="CASCADE"))

memories = table("memories",
    Column("id", String(32), primary_key=True),
    Column("project_id", String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    Column("filename", String(255), nullable=False), Column("name", Text, nullable=False),
    Column("type", String(24), nullable=False), Column("description", body_type, nullable=False),
    Column("body", body_type, nullable=False), Column("document", body_type, nullable=False),
    Column("source_session_id", String(32), ForeignKey("sessions.id", ondelete="SET NULL")),
    Column("source_run_id", String(32), ForeignKey("agent_runs.id", ondelete="SET NULL")),
    Column("version", Integer, nullable=False, default=1), Column("updated_at", timestamp_type, nullable=False),
    UniqueConstraint("project_id", "filename"))

artifacts = table("artifacts",
    Column("id", String(32), primary_key=True),
    Column("project_id", String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
    Column("session_id", String(32), ForeignKey("sessions.id", ondelete="SET NULL")),
    Column("run_id", String(32), ForeignKey("agent_runs.id", ondelete="SET NULL")),
    Column("kind", String(32), nullable=False), Column("path", Text),
    Column("path_key", String(64)), Column("content", body_type), Column("line_count", BigInteger),
    Column("size_bytes", BigInteger, nullable=False),
    Column("sha256", String(64), nullable=False), Column("created_at", timestamp_type, nullable=False),
    UniqueConstraint("project_id", "path_key"))

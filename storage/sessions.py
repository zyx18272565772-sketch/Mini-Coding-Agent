"""会话短事务：原文、事件、有效上下文与用量分别保存，重试复用运行 ID。"""
from collections import defaultdict
import re
from uuid import uuid4, uuid5, NAMESPACE_URL

from sqlalchemy import delete, func, select, update

from desktop_app.store import SessionStore, to_plain
from llm.usage import COUNTERS, UsageRun
from storage.database import StorageError, digest, iso, timestamp, upsert, utcnow
from storage.models import (agent_runs, chat_messages, context_checkpoints, project_tasks,
                            projects, run_usage, session_events, sessions)
from storage.projects import ensure_project


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("会话或运行编号无效")
    return value


def save_chat(connection, sid, events, default_run):
    """把本批流式片段合并到一条原文；终稿覆盖片段，避免重复展示。"""
    changed = {}
    for event in events:
        kind = event["type"]
        if kind not in ("user", "assistant", "assistant_start", "assistant_delta", "assistant_end"):
            continue
        scope = event.get("scope", "main")
        role = "user" if kind == "user" else "assistant"
        key = f"{scope}:{event.get('message_id') or role + ':' + str(event['seq'])}"
        if key not in changed:
            old = connection.execute(select(chat_messages).where(
                chat_messages.c.session_id == sid, chat_messages.c.message_key == key)).mappings().first()
            changed[key] = dict(old) if old else dict(id=uuid5(NAMESPACE_URL, sid + key).hex,
                session_id=sid, run_id=event.get("run_id", default_run), message_key=key,
                seq=event["seq"], role=role, scope=scope, body="", status="streaming",
                created_at=timestamp(event.get("time")))
        row = changed[key]
        if kind == "assistant_delta":
            row["body"] += event.get("text", "")
        elif kind in ("user", "assistant"):
            row["body"] = event.get("text", "")
            row["status"] = "completed"
        elif kind == "assistant_end" and event.get("interrupted"):
            row["status"] = "interrupted"
    for row in changed.values():
        upsert(connection, chat_messages, row, ("id",))


class SQLSessionStore(SessionStore):
    def __init__(self, database):
        self.database = database
        self.warnings = []

    def save(self, session):
        sid = identifier(session["id"])
        plain = to_plain(session)
        with self.database.transaction() as c:
            project_id = ensure_project(c, plain["workdir"], project_id=plain.get("project_id"))
            old = c.execute(select(sessions).where(sessions.c.id == sid).with_for_update()).mappings().first()
            if old and plain.get("_db_version") != old["version"]:
                raise StorageError("会话已被另一个程序更新，请重新打开会话后继续；本次未覆盖数据库。")
            if old is None and plain.get("_db_version") is not None:
                raise StorageError("会话已被删除，本次未重新创建。")
            version = (old["version"] if old else 0) + 1
            row = dict(id=sid, project_id=project_id, title=plain["title"], title_custom=bool(plain.get("title_custom")),
                workdir=plain["workdir"], status=plain["status"], version=version,
                created_at=timestamp(plain["created_at"]), updated_at=timestamp(plain["updated_at"]))
            if old is None:
                c.execute(sessions.insert().values(**row))
            else:
                c.execute(update(sessions).where(sessions.c.id == sid).values(**row))

            rid = plain.get("run_id") or (plain.get("usage") or {}).get("run_id")
            all_usage = dict(plain.get("usage_runs") or {})
            if plain.get("usage"):
                all_usage[plain["usage"]["run_id"]] = plain["usage"]
            hashes = {run_id: digest(usage) for run_id, usage in all_usage.items()}
            changed_usage = {run_id: usage for run_id, usage in all_usage.items()
                             if hashes[run_id] != plain.get("_db_usage_hashes", {}).get(run_id)}
            run_ids = list(dict.fromkeys([*changed_usage, *([rid] if rid else [])]))
            for run_id in run_ids:
                identifier(run_id)
                existing = c.execute(select(agent_runs).where(agent_runs.c.id == run_id)).mappings().first()
                if existing and existing["session_id"] != sid:
                    raise ValueError("运行编号属于其他会话")
                current = run_id == rid
                status = plain["status"] if current else all_usage[run_id].get("status", "completed")
                values = dict(id=run_id, session_id=sid, request=plain.get("active_request", "") if current else "",
                    model=plain.get("model", ""), workdir=plain.get("run_workdir", plain["workdir"]), status=status,
                    failure=plain.get("failure") if current else None, attempt=plain.get("run_attempt", 1) if current else 1,
                    started_at=existing["started_at"] if existing else timestamp(plain["updated_at"]),
                    ended_at=None if status in ("running", "waiting", "stopping") else timestamp(plain["updated_at"]))
                if existing:
                    if not current:
                        continue
                    values["model"] = values["model"] or existing["model"]
                upsert(c, agent_runs, values, ("id", "session_id", "started_at"))

            for run_id, usage in changed_usage.items():
                for purpose, bucket in usage.get("by_purpose", {}).items():
                    values = {key: bucket.get(key, 0) for key in COUNTERS}
                    if any(type(value) is not int or value < 0 for value in values.values()):
                        raise ValueError("已保存的用量记录无效")
                    upsert(c, run_usage, dict(run_id=run_id, purpose=purpose, **values, updated_at=utcnow()), ("run_id", "purpose"))

            last_seq = c.execute(select(func.max(session_events.c.seq)).where(session_events.c.session_id == sid)).scalar() or 0
            events = plain["events"]
            if [e["seq"] for e in events] != list(range(1, len(events) + 1)) or len(events) < last_seq:
                raise ValueError("会话事件顺序无效，未写入数据库")
            new_events = events[last_seq:]
            if new_events:
                c.execute(session_events.insert(), [dict(id=uuid4().hex, session_id=sid,
                    run_id=e.get("run_id", rid), seq=e["seq"], event_type=e["type"], payload=e,
                    created_at=timestamp(e.get("time"))) for e in new_events])
                save_chat(c, sid, new_events, rid)

            context_hash = digest(plain["messages"])
            checkpoint_id = old["latest_checkpoint_id"] if old else None
            if old is None or old["context_hash"] != context_hash:
                checkpoint_id = uuid4().hex
                c.execute(context_checkpoints.insert().values(id=checkpoint_id, session_id=sid, run_id=rid,
                    version=version, messages_json=plain["messages"], schema_version=1, created_at=utcnow()))
                # 保留最近三个有效快照；聊天原文与事件不随上下文压缩删除。
                keep = c.execute(select(context_checkpoints.c.id).where(context_checkpoints.c.session_id == sid)
                    .order_by(context_checkpoints.c.version.desc()).limit(3)).scalars().all()
                c.execute(delete(context_checkpoints).where(context_checkpoints.c.session_id == sid,
                    context_checkpoints.c.id.not_in(keep)))
            c.execute(update(sessions).where(sessions.c.id == sid).values(latest_checkpoint_id=checkpoint_id,
                current_run_id=rid, context_hash=context_hash))
            if plain["status"] not in ("running", "waiting", "stopping"):
                c.execute(update(chat_messages).where(chat_messages.c.session_id == sid,
                    chat_messages.c.status == "streaming").values(status="interrupted"))
        # 只有提交成功后才推进客户端版本，失败仍可重试同一快照。
        session.update(_db_version=version, _db_usage_hashes=hashes, project_id=project_id)

    def load_all(self):
        result = []
        with self.database.transaction() as c:
            rows = c.execute(select(sessions).order_by(sessions.c.created_at)).mappings().all()
            if not rows:
                return []
            ids = [r["id"] for r in rows]
            checkpoints = dict(c.execute(select(context_checkpoints.c.id, context_checkpoints.c.messages_json).where(
                context_checkpoints.c.id.in_([r["latest_checkpoint_id"] for r in rows if r["latest_checkpoint_id"]]))).all())
            events_by_session, runs_by_session, buckets_by_run = defaultdict(list), defaultdict(list), defaultdict(list)
            for event in c.execute(select(session_events.c.session_id, session_events.c.payload).where(
                    session_events.c.session_id.in_(ids)).order_by(session_events.c.seq)).mappings():
                events_by_session[event["session_id"]].append(event["payload"])
            runs = c.execute(select(agent_runs).where(agent_runs.c.session_id.in_(ids))).mappings().all()
            for run in runs:
                runs_by_session[run["session_id"]].append(run)
            if runs:
                for bucket in c.execute(select(run_usage).where(run_usage.c.run_id.in_([r["id"] for r in runs]))).mappings():
                    buckets_by_run[bucket["run_id"]].append(bucket)
            for row in rows:
                sid = row["id"]
                checkpoint = checkpoints.get(row["latest_checkpoint_id"])
                usage_runs = {}
                for run in runs_by_session[sid]:
                    buckets = buckets_by_run[run["id"]]
                    if buckets:
                        usage = UsageRun(previous={"run_id": run["id"], "by_purpose": {
                            b["purpose"]: {k: b[k] for k in COUNTERS} for b in buckets}})
                        usage.status = run["status"]
                        usage_runs[run["id"]] = usage.snapshot()
                active = next((r for r in runs_by_session[sid] if r["id"] == row["current_run_id"]), {})
                result.append(dict(version=1, id=sid, title=row["title"], title_custom=row["title_custom"],
                    project_id=row["project_id"], workdir=row["workdir"], status=row["status"],
                    created_at=iso(row["created_at"]), updated_at=iso(row["updated_at"]),
                    messages=checkpoint or [], events=events_by_session[sid],
                    usage_runs=usage_runs, usage=usage_runs.get(row["current_run_id"]), run_id=row["current_run_id"],
                    active_request=active.get("request", ""), run_workdir=active.get("workdir", row["workdir"]),
                    failure=active.get("failure"), model=active.get("model", ""), run_attempt=active.get("attempt", 0),
                    _db_version=row["version"], _db_usage_hashes={rid: digest(u) for rid, u in usage_runs.items()}))
        return result

    def delete(self, session_id):
        identifier(session_id)
        with self.database.transaction() as c:
            project_id = c.execute(select(sessions.c.project_id).where(sessions.c.id == session_id)).scalar()
            if project_id is None:
                return
            c.execute(select(projects.c.id).where(projects.c.id == project_id).with_for_update()).one()
            c.execute(update(project_tasks).where(project_tasks.c.owner_session_id == session_id,
                project_tasks.c.status == "in_progress").values(status="pending", owner_session_id=None, updated_at=utcnow()))
            c.execute(delete(sessions).where(sessions.c.id == session_id))

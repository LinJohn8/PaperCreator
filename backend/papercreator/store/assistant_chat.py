"""Durable, project-scoped assistant conversations."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from sqlite3 import Connection
from typing import Any

from ..core.config import get_settings
from ..core.db import connect, dumps, execute, loads, query, query_one, transaction
from ..core.errors import NotFoundError, ValidationError
from ..core.util import new_id, utc_now, utc_now_iso
from . import projects as projects_store


_IMPORT_MAX_BYTES = 64 * 1024 * 1024
_IMPORT_MAX_THREADS = 2_000
_IMPORT_MAX_MESSAGES = 100_000
_IMPORT_MAX_MESSAGE_CHARS = 40_000


def _thread(row: Any) -> dict[str, Any]:
    data = dict(row)
    for field in ("message_count", "character_count", "estimated_bytes"):
        if field in data:
            data[field] = int(data[field] or 0)
    return data


def _message(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["actions"] = loads(data.get("actions"), []) or []
    data["meta"] = loads(data.get("meta"), {}) or {}
    return data


def create(*, project_id: str = "", title: str = "") -> dict[str, Any]:
    if project_id:
        projects_store.require(project_id)
    now = utc_now_iso()
    thread_id = new_id("chat")
    execute(
        "INSERT INTO assistant_threads (id,project_id,title,created_at,updated_at) "
        "VALUES (?,?,?,?,?)",
        (thread_id, project_id or None, title.strip()[:160], now, now),
    )
    return require(thread_id)


def list_threads(project_id: str = "") -> list[dict[str, Any]]:
    where, params = _scope_clause(project_id)
    rows = query(
        "SELECT t.*,COUNT(m.id) AS message_count,"
        "COALESCE(SUM(LENGTH(m.content)),0) AS character_count,"
        "COALESCE(SUM(LENGTH(CAST(m.content AS BLOB))),0) AS estimated_bytes,"
        "COALESCE(MAX(m.created_at),t.updated_at) AS last_activity "
        "FROM assistant_threads t LEFT JOIN assistant_messages m ON m.thread_id=t.id "
        f"WHERE {where} GROUP BY t.id ORDER BY last_activity DESC LIMIT 100",
        params,
    )
    return [_thread(row) for row in rows]


def _scope_clause(project_id: str, *, alias: str = "t") -> tuple[str, tuple[Any, ...]]:
    if project_id:
        projects_store.require(project_id)
        return f"{alias}.project_id=?", (project_id,)
    return f"{alias}.project_id IS NULL", ()


def get(thread_id: str) -> dict[str, Any] | None:
    row = query_one("SELECT * FROM assistant_threads WHERE id=?", (thread_id,))
    return _thread(row) if row else None


def require(thread_id: str) -> dict[str, Any]:
    item = get(thread_id)
    if item is None:
        raise NotFoundError(f"assistant thread {thread_id} not found")
    return item


def messages(thread_id: str, limit: int | None = 200) -> list[dict[str, Any]]:
    require(thread_id)
    if limit is None:
        rows = query(
            "SELECT * FROM assistant_messages WHERE thread_id=? ORDER BY ordering",
            (thread_id,),
        )
    else:
        rows = query(
            "SELECT * FROM (SELECT * FROM assistant_messages WHERE thread_id=? "
            "ORDER BY ordering DESC LIMIT ?) ORDER BY ordering",
            (thread_id, max(1, min(limit, 1000))),
        )
    return [_message(row) for row in rows]


def scope_stats(project_id: str = "") -> dict[str, Any]:
    where, params = _scope_clause(project_id)
    row = query_one(
        "SELECT COUNT(DISTINCT t.id) AS thread_count,COUNT(m.id) AS message_count,"
        "COALESCE(SUM(CASE WHEN m.role='user' THEN 1 ELSE 0 END),0) AS user_messages,"
        "COALESCE(SUM(CASE WHEN m.role='assistant' THEN 1 ELSE 0 END),0) AS assistant_messages,"
        "COALESCE(SUM(LENGTH(m.content)),0) AS character_count,"
        "COALESCE(SUM(LENGTH(CAST(m.content AS BLOB))),0) AS estimated_bytes,"
        "MIN(t.created_at) AS first_activity,"
        "MAX(COALESCE(m.created_at,t.updated_at)) AS last_activity "
        "FROM assistant_threads t LEFT JOIN assistant_messages m ON m.thread_id=t.id "
        f"WHERE {where}",
        params,
    )
    data = dict(row) if row else {}
    for field in (
        "thread_count", "message_count", "user_messages", "assistant_messages",
        "character_count", "estimated_bytes",
    ):
        data[field] = int(data.get(field) or 0)
    data.setdefault("first_activity", None)
    data.setdefault("last_activity", None)
    return data


def export_scope(project_id: str = "") -> dict[str, Any]:
    if project_id:
        project = projects_store.require(project_id)
        scope = {"kind": "project", "project_id": project_id, "title": project.title}
    else:
        scope = {"kind": "workbench", "project_id": None, "title": ""}
    where, params = _scope_clause(project_id)
    rows = query(
        "SELECT t.*,COUNT(m.id) AS message_count,"
        "COALESCE(SUM(LENGTH(m.content)),0) AS character_count,"
        "COALESCE(SUM(LENGTH(CAST(m.content AS BLOB))),0) AS estimated_bytes,"
        "COALESCE(MAX(m.created_at),t.updated_at) AS last_activity "
        "FROM assistant_threads t LEFT JOIN assistant_messages m ON m.thread_id=t.id "
        f"WHERE {where} GROUP BY t.id ORDER BY t.created_at,t.id",
        params,
    )
    threads = []
    for row in rows:
        thread = _thread(row)
        thread["messages"] = messages(thread["id"], None)
        threads.append(thread)
    return {
        "format": "papercreator.assistant_conversations",
        "format_version": 1,
        "exported_at": utc_now_iso(),
        "scope": scope,
        "retention_days": get_settings().assistant.retention_days,
        "stats": scope_stats(project_id),
        "threads": threads,
    }


def _import_scope_key(project_id: str) -> str:
    return f"project:{project_id}" if project_id else "workbench"


def _import_fingerprint(canonical: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _matching_local_source(
    conn: Connection,
    project_id: str,
    source_thread: dict[str, Any],
) -> str | None:
    scope_sql = "project_id=?" if project_id else "project_id IS NULL"
    scope_params: tuple[Any, ...] = (project_id,) if project_id else ()
    row = conn.execute(
        f"SELECT * FROM assistant_threads WHERE id=? AND {scope_sql}",
        (source_thread["source_thread_id"], *scope_params),
    ).fetchone()
    if row is None:
        return None
    message_rows = conn.execute(
        "SELECT * FROM assistant_messages WHERE thread_id=? ORDER BY ordering",
        (source_thread["source_thread_id"],),
    ).fetchall()
    canonical = {
        "source_thread_id": str(row["id"]),
        "title": str(row["title"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "messages": [
            {
                "role": str(message["role"]),
                "content": str(message["content"]),
                "actions": loads(message["actions"], []) or [],
                "meta": loads(message["meta"], {}) or {},
                "created_at": str(message["created_at"] or ""),
            }
            for message in message_rows
        ],
    }
    return str(row["id"]) if _import_fingerprint(canonical) == source_thread["fingerprint"] else None


def _normalise_import(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if project_id:
        projects_store.require(project_id)
    if not isinstance(payload, dict):
        raise ValidationError("assistant conversation import must be a JSON object")
    try:
        payload_bytes = len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"assistant conversation import is not valid JSON: {exc}") from exc
    if payload_bytes > _IMPORT_MAX_BYTES:
        raise ValidationError("assistant conversation import exceeds the 64 MiB limit")
    if payload.get("format") != "papercreator.assistant_conversations":
        raise ValidationError("unsupported assistant conversation import format")
    if payload.get("format_version") != 1:
        raise ValidationError("only assistant conversation format_version 1 is supported")
    source_scope = payload.get("scope")
    if not isinstance(source_scope, dict) or source_scope.get("kind") not in {
        "workbench", "project",
    }:
        raise ValidationError("assistant conversation import has an invalid source scope")
    raw_threads = payload.get("threads")
    if not isinstance(raw_threads, list):
        raise ValidationError("assistant conversation import threads must be a list")
    if len(raw_threads) > _IMPORT_MAX_THREADS:
        raise ValidationError(f"assistant conversation import exceeds {_IMPORT_MAX_THREADS} threads")

    threads: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    message_count = 0
    character_count = 0
    estimated_bytes = 0
    for thread_index, raw_thread in enumerate(raw_threads):
        if not isinstance(raw_thread, dict):
            raise ValidationError(f"assistant import thread {thread_index + 1} must be an object")
        source_id = str(raw_thread.get("id") or "").strip()
        if not source_id or len(source_id) > 200:
            raise ValidationError(f"assistant import thread {thread_index + 1} has an invalid id")
        if source_id in source_ids:
            raise ValidationError(f"assistant import contains duplicate thread id {source_id}")
        source_ids.add(source_id)
        title = str(raw_thread.get("title") or "").strip()[:160]
        created_at = str(raw_thread.get("created_at") or "").strip()
        updated_at = str(raw_thread.get("updated_at") or "").strip()
        if len(created_at) > 64 or len(updated_at) > 64:
            raise ValidationError(f"assistant import thread {source_id} has an invalid timestamp")
        raw_messages = raw_thread.get("messages")
        if not isinstance(raw_messages, list):
            raise ValidationError(f"assistant import thread {source_id} messages must be a list")
        messages_data: list[dict[str, Any]] = []
        for message_index, raw_message in enumerate(raw_messages):
            if not isinstance(raw_message, dict):
                raise ValidationError(
                    f"assistant import message {message_index + 1} in {source_id} must be an object"
                )
            role = raw_message.get("role")
            if role not in {"user", "assistant"}:
                raise ValidationError(
                    f"assistant import message {message_index + 1} in {source_id} has an invalid role"
                )
            content = raw_message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValidationError(
                    f"assistant import message {message_index + 1} in {source_id} is empty"
                )
            if len(content) > _IMPORT_MAX_MESSAGE_CHARS:
                raise ValidationError(
                    f"assistant import message {message_index + 1} in {source_id} exceeds "
                    f"{_IMPORT_MAX_MESSAGE_CHARS} characters"
                )
            actions = raw_message.get("actions", [])
            meta = raw_message.get("meta", {})
            if not isinstance(actions, list) or len(actions) > 100:
                raise ValidationError(
                    f"assistant import message {message_index + 1} in {source_id} has invalid actions"
                )
            if not isinstance(meta, dict):
                raise ValidationError(
                    f"assistant import message {message_index + 1} in {source_id} has invalid meta"
                )
            structured_bytes = len(dumps({"actions": actions, "meta": meta}).encode("utf-8"))
            if structured_bytes > 1024 * 1024:
                raise ValidationError(
                    f"assistant import message {message_index + 1} in {source_id} metadata is too large"
                )
            message_created_at = str(raw_message.get("created_at") or "").strip()
            if len(message_created_at) > 64:
                raise ValidationError(
                    f"assistant import message {message_index + 1} in {source_id} has an invalid timestamp"
                )
            messages_data.append({
                "role": role,
                "content": content,
                "actions": actions,
                "meta": meta,
                "created_at": message_created_at,
            })
            message_count += 1
            character_count += len(content)
            estimated_bytes += len(content.encode("utf-8")) + structured_bytes
            if message_count > _IMPORT_MAX_MESSAGES:
                raise ValidationError(
                    f"assistant conversation import exceeds {_IMPORT_MAX_MESSAGES} messages"
                )
        canonical = {
            "source_thread_id": source_id,
            "title": title,
            "created_at": created_at,
            "updated_at": updated_at,
            "messages": messages_data,
        }
        fingerprint = _import_fingerprint(canonical)
        threads.append({**canonical, "fingerprint": fingerprint})

    normalised = {
        "target_scope": _import_scope_key(project_id),
        "source_scope": source_scope,
        "threads": threads,
    }
    preview_token = hashlib.sha256(
        json.dumps(
            normalised, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        **normalised,
        "preview_token": preview_token,
        "stats": {
            "thread_count": len(threads),
            "message_count": message_count,
            "character_count": character_count,
            "estimated_bytes": estimated_bytes,
        },
    }


def preview_import(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalised = _normalise_import(project_id, payload)
    existing = 0
    conn = connect()
    for thread in normalised["threads"]:
        row = conn.execute(
            "SELECT 1 FROM assistant_thread_imports i "
            "JOIN assistant_threads t ON t.id=i.thread_id "
            "WHERE i.scope_key=? AND i.source_thread_id=? AND i.source_fingerprint=?",
            (normalised["target_scope"], thread["source_thread_id"], thread["fingerprint"]),
        ).fetchone()
        existing += int(
            row is not None or _matching_local_source(conn, project_id, thread) is not None
        )
    return {
        "target_scope": {
            "kind": "project" if project_id else "workbench",
            "project_id": project_id or None,
        },
        "source_scope": normalised["source_scope"],
        "preview_token": normalised["preview_token"],
        "stats": {
            **normalised["stats"],
            "new_threads": normalised["stats"]["thread_count"] - existing,
            "already_imported_threads": existing,
        },
    }


def import_scope(
    project_id: str, payload: dict[str, Any], *, preview_token: str
) -> dict[str, Any]:
    if len(preview_token) != 64:
        raise ValidationError("a valid assistant import preview token is required")
    normalised = _normalise_import(project_id, payload)
    if normalised["preview_token"] != preview_token:
        raise ValidationError("assistant conversation archive changed after preview")
    now = utc_now_iso()
    imported_ids: list[str] = []
    skipped = 0
    imported_messages = 0
    with transaction() as conn:
        for thread in normalised["threads"]:
            existing = conn.execute(
                "SELECT thread_id FROM assistant_thread_imports "
                "WHERE scope_key=? AND source_thread_id=? AND source_fingerprint=?",
                (
                    normalised["target_scope"], thread["source_thread_id"],
                    thread["fingerprint"],
                ),
            ).fetchone()
            if existing is not None:
                skipped += 1
                continue
            local_source_id = _matching_local_source(conn, project_id, thread)
            if local_source_id is not None:
                conn.execute(
                    "INSERT INTO assistant_thread_imports "
                    "(id,scope_key,source_thread_id,source_fingerprint,thread_id,source_scope,imported_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        new_id("chatimp"), normalised["target_scope"],
                        thread["source_thread_id"], thread["fingerprint"], local_source_id,
                        dumps(normalised["source_scope"]), now,
                    ),
                )
                skipped += 1
                continue
            thread_id = new_id("chat")
            created_at = thread["created_at"] or now
            updated_at = thread["updated_at"] or created_at
            conn.execute(
                "INSERT INTO assistant_threads (id,project_id,title,created_at,updated_at) "
                "VALUES (?,?,?,?,?)",
                (thread_id, project_id or None, thread["title"], created_at, updated_at),
            )
            for ordering, message in enumerate(thread["messages"], start=1):
                conn.execute(
                    "INSERT INTO assistant_messages "
                    "(id,thread_id,ordering,role,content,actions,meta,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        new_id("msg"), thread_id, ordering, message["role"],
                        message["content"], dumps(message["actions"]),
                        dumps(message["meta"]), message["created_at"] or created_at,
                    ),
                )
            conn.execute(
                "INSERT INTO assistant_thread_imports "
                "(id,scope_key,source_thread_id,source_fingerprint,thread_id,source_scope,imported_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    new_id("chatimp"), normalised["target_scope"],
                    thread["source_thread_id"], thread["fingerprint"], thread_id,
                    dumps(normalised["source_scope"]), now,
                ),
            )
            imported_ids.append(thread_id)
            imported_messages += len(thread["messages"])
    return {
        "target_scope": {
            "kind": "project" if project_id else "workbench",
            "project_id": project_id or None,
        },
        "imported_threads": len(imported_ids),
        "imported_messages": imported_messages,
        "skipped_threads": skipped,
        "thread_ids": imported_ids,
    }


def _candidate_snapshot(
    project_id: str,
    *,
    mode: str,
    cutoff: str | None,
    conn: Connection | None = None,
) -> dict[str, Any]:
    if mode not in {"all", "retention"}:
        raise ValidationError("assistant maintenance mode must be 'all' or 'retention'")
    if mode == "retention" and not cutoff:
        raise ValidationError("retention maintenance requires an explicit cutoff")
    where, params = _scope_clause(project_id)
    if mode == "retention":
        where += " AND t.updated_at<?"
        params += (cutoff,)
    connection = conn or connect()
    rows = connection.execute(
        "SELECT t.id AS thread_id,t.project_id,t.title,t.created_at AS thread_created_at,"
        "t.updated_at AS thread_updated_at,m.id AS message_id,m.ordering,m.role,m.content,"
        "m.actions,m.meta,m.created_at AS message_created_at "
        "FROM assistant_threads t LEFT JOIN assistant_messages m ON m.thread_id=t.id "
        f"WHERE {where} ORDER BY t.id,m.ordering,m.id",
        params,
    ).fetchall()
    digest = hashlib.sha256()
    digest.update(dumps({
        "scope": project_id or None,
        "mode": mode,
        "cutoff": cutoff,
    }).encode("utf-8"))
    thread_ids: list[str] = []
    seen: set[str] = set()
    message_count = 0
    character_count = 0
    estimated_bytes = 0
    first_activity: str | None = None
    last_activity: str | None = None
    for row in rows:
        data = dict(row)
        thread_id = str(data["thread_id"])
        if thread_id not in seen:
            seen.add(thread_id)
            thread_ids.append(thread_id)
            created = str(data["thread_created_at"])
            updated = str(data["thread_updated_at"])
            first_activity = min(first_activity, created) if first_activity else created
            last_activity = max(last_activity, updated) if last_activity else updated
        if data.get("message_id"):
            content = str(data.get("content") or "")
            message_count += 1
            character_count += len(content)
            estimated_bytes += len(content.encode("utf-8"))
            activity = str(data.get("message_created_at") or data["thread_updated_at"])
            last_activity = max(last_activity, activity) if last_activity else activity
        digest.update(b"\x1e")
        digest.update(dumps(data).encode("utf-8"))
    return {
        "preview_token": digest.hexdigest(),
        "thread_ids": thread_ids,
        "stats": {
            "thread_count": len(thread_ids),
            "message_count": message_count,
            "character_count": character_count,
            "estimated_bytes": estimated_bytes,
            "first_activity": first_activity,
            "last_activity": last_activity,
        },
    }


def preview_maintenance(
    project_id: str = "", *, mode: str, older_than_days: int = 0
) -> dict[str, Any]:
    cutoff: str | None = None
    if mode == "retention":
        if not 1 <= older_than_days <= 3650:
            raise ValidationError("retention days must be between 1 and 3650")
        cutoff = (utc_now() - timedelta(days=older_than_days)).replace(microsecond=0).isoformat()
    snapshot = _candidate_snapshot(project_id, mode=mode, cutoff=cutoff)
    return {
        "scope": {"kind": "project" if project_id else "workbench", "project_id": project_id or None},
        "mode": mode,
        "older_than_days": older_than_days if mode == "retention" else 0,
        "cutoff": cutoff,
        "preview_token": snapshot["preview_token"],
        "stats": snapshot["stats"],
    }


def execute_maintenance(
    project_id: str = "",
    *,
    mode: str,
    cutoff: str | None,
    preview_token: str,
) -> dict[str, Any]:
    if len(preview_token) != 64:
        raise ValidationError("a valid assistant maintenance preview token is required")
    with transaction() as conn:
        snapshot = _candidate_snapshot(
            project_id, mode=mode, cutoff=cutoff, conn=conn
        )
        if snapshot["preview_token"] != preview_token:
            raise ValidationError(
                "assistant conversations changed after preview; preview the deletion again"
            )
        where, params = _scope_clause(project_id)
        if mode == "retention":
            if not cutoff:
                raise ValidationError("retention maintenance requires an explicit cutoff")
            where += " AND updated_at<?"
            params += (cutoff,)
        deleted = conn.execute(
            f"DELETE FROM assistant_threads WHERE {where.replace('t.', '')}", params
        ).rowcount
    return {
        "deleted_threads": int(deleted or 0),
        "deleted_messages": snapshot["stats"]["message_count"],
        "scope": {"kind": "project" if project_id else "workbench", "project_id": project_id or None},
        "mode": mode,
        "cutoff": cutoff,
    }


def _message_redaction_snapshot(
    message_id: str, *, conn: Connection | None = None
) -> dict[str, Any]:
    connection = conn or connect()
    row = connection.execute(
        "SELECT m.*,t.project_id,t.title AS thread_title "
        "FROM assistant_messages m JOIN assistant_threads t ON t.id=m.thread_id "
        "WHERE m.id=?",
        (message_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(f"assistant message {message_id} not found")
    data = dict(row)
    meta = loads(data.get("meta"), {}) or {}
    if isinstance(meta, dict) and meta.get("redaction"):
        raise ValidationError("assistant message content has already been redacted")
    digest = hashlib.sha256(dumps(data).encode("utf-8")).hexdigest()
    content = str(data.get("content") or "")
    actions = loads(data.get("actions"), []) or []
    return {
        "preview_token": digest,
        "message_id": message_id,
        "thread_id": str(data["thread_id"]),
        "thread_title": str(data.get("thread_title") or ""),
        "project_id": data.get("project_id"),
        "role": str(data["role"]),
        "created_at": str(data["created_at"]),
        "character_count": len(content),
        "estimated_bytes": len(content.encode("utf-8")),
        "actions_count": len(actions) if isinstance(actions, list) else 0,
        "has_meta": bool(meta),
        "original_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def preview_message_redaction(message_id: str) -> dict[str, Any]:
    return _message_redaction_snapshot(message_id)


def redact_message(
    message_id: str, *, preview_token: str, reason: str = ""
) -> dict[str, Any]:
    if len(preview_token) != 64:
        raise ValidationError("a valid assistant message redaction preview token is required")
    reason = reason.strip()
    if len(reason) > 200:
        raise ValidationError("assistant message redaction reason exceeds 200 characters")
    redacted_at = utc_now_iso()
    with transaction() as conn:
        snapshot = _message_redaction_snapshot(message_id, conn=conn)
        if snapshot["preview_token"] != preview_token:
            raise ValidationError(
                "assistant message changed after redaction preview; preview it again"
            )
        audit = {
            "redacted_at": redacted_at,
            "reason": reason,
            "original_sha256": snapshot["original_sha256"],
            "original_characters": snapshot["character_count"],
            "original_bytes": snapshot["estimated_bytes"],
            "actions_removed": snapshot["actions_count"],
            "meta_removed": snapshot["has_meta"],
        }
        conn.execute(
            "UPDATE assistant_messages SET content=?,actions='[]',meta=? WHERE id=?",
            ("[Content removed by user]", dumps({"redaction": audit}), message_id),
        )
        conn.execute(
            "UPDATE assistant_threads SET updated_at=? WHERE id=?",
            (redacted_at, snapshot["thread_id"]),
        )
    return {
        "message_id": message_id,
        "thread_id": snapshot["thread_id"],
        "redacted_at": redacted_at,
        "audit": audit,
    }


def append_exchange(
    thread_id: str,
    *,
    user_text: str,
    assistant_text: str,
    actions: list[dict[str, Any]],
    meta: dict[str, Any],
) -> dict[str, Any]:
    thread = require(thread_id)
    user_text = user_text.strip()
    assistant_text = assistant_text.strip()
    if not user_text or not assistant_text:
        raise ValidationError("assistant exchange messages must not be empty")
    now = utc_now_iso()
    with transaction() as conn:
        ordering = int(conn.execute(
            "SELECT COALESCE(MAX(ordering),0) FROM assistant_messages WHERE thread_id=?",
            (thread_id,),
        ).fetchone()[0]) + 1
        conn.execute(
            "INSERT INTO assistant_messages (id,thread_id,ordering,role,content,actions,meta,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (new_id("msg"), thread_id, ordering, "user", user_text, "[]", "{}", now),
        )
        conn.execute(
            "INSERT INTO assistant_messages (id,thread_id,ordering,role,content,actions,meta,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (new_id("msg"), thread_id, ordering + 1, "assistant", assistant_text, dumps(actions), dumps(meta), now),
        )
        title = thread["title"] or user_text.replace("\n", " ")[:80]
        conn.execute(
            "UPDATE assistant_threads SET title=?,updated_at=? WHERE id=?",
            (title, now, thread_id),
        )
    return require(thread_id)


def delete(thread_id: str) -> bool:
    require(thread_id)
    return bool(execute("DELETE FROM assistant_threads WHERE id=?", (thread_id,)).rowcount)

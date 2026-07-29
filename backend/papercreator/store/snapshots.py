"""Manuscript snapshots for version comparison.

A snapshot is a full copy of every section's text at a point in time, stored as
JSON in one row. Git already versions the on-disk files; snapshots exist because:

* they capture the *database* state, including per-section metadata that the
  flattened files do not carry;
* they are taken automatically around agent runs (``pre_agent`` / ``post_agent``)
  so "what did the AI change?" is answerable without a git commit per step;
* diffing them needs no git repository, so version compare works even for
  projects with git disabled.

Snapshot payloads are small (a full paper is ~50 KB of text) so the simple
approach is correct here; :func:`prune` bounds growth.
"""

from __future__ import annotations

import difflib
from typing import Any

from ..core.db import dumps, execute, loads, query, query_one, row_to_dict
from ..core.errors import NotFoundError
from ..core.util import new_id, utc_now_iso, word_count
from . import documents as documents_store


def capture(
    project_id: str,
    *,
    label: str = "",
    kind: str = "manual",
    git_commit: str = "",
) -> dict[str, Any]:
    """Snapshot every document/section of a project.

    Payload shape::

        {document_id: {"title": str, "format": str,
                       "sections": {key: {title, content, content_zh, status,
                                          word_count, ordering}}}}
    """
    payload: dict[str, Any] = {}
    total_words = 0
    for document in documents_store.list_documents(project_id):
        sections: dict[str, Any] = {}
        for section in documents_store.list_sections(document.id):
            sections[section.key] = {
                "title": section.title,
                "title_zh": section.title_zh,
                "content": section.content,
                "content_zh": section.content_zh,
                "status": section.status,
                "word_count": section.word_count,
                "ordering": section.ordering,
                "level": section.level,
            }
            total_words += section.word_count
        payload[document.id] = {
            "title": document.title,
            "format": document.format,
            "rel_path": document.rel_path,
            "sections": sections,
        }
    sid = new_id("snp")
    stats = {
        "documents": len(payload),
        "sections": sum(len(d["sections"]) for d in payload.values()),
        "words": total_words,
    }
    execute(
        "INSERT INTO snapshots (id, project_id, label, kind, git_commit, payload,"
        " stats, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (sid, project_id, label, kind, git_commit, dumps(payload), dumps(stats),
         utc_now_iso()),
    )
    return {"id": sid, "stats": stats, "label": label, "kind": kind}


def get(snapshot_id: str) -> dict[str, Any] | None:
    row = row_to_dict(query_one("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)))
    if row is None:
        return None
    row["payload"] = loads(row.get("payload"), {}) or {}
    row["stats"] = loads(row.get("stats"), {}) or {}
    return row


def require(snapshot_id: str) -> dict[str, Any]:
    snapshot = get(snapshot_id)
    if snapshot is None:
        raise NotFoundError(f"snapshot {snapshot_id} not found")
    return snapshot


def list_snapshots(project_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Metadata only - payloads are excluded from list responses."""
    rows = query(
        "SELECT id, project_id, label, kind, git_commit, stats, created_at"
        " FROM snapshots WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
        (project_id, limit),
    )
    out = []
    for row in rows:
        item = dict(row)
        item["stats"] = loads(item.get("stats"), {}) or {}
        out.append(item)
    return out


def delete(snapshot_id: str) -> bool:
    cur = execute("DELETE FROM snapshots WHERE id=?", (snapshot_id,))
    return bool(cur.rowcount)


def prune(project_id: str, keep: int = 50) -> int:
    """Drop the oldest snapshots beyond ``keep``, preserving manual ones.

    Auto snapshots are cheap to lose; a snapshot the user explicitly labelled is
    not, so those are never pruned.
    """
    row = query_one(
        "SELECT created_at FROM snapshots WHERE project_id=? AND kind != 'manual'"
        " ORDER BY created_at DESC LIMIT 1 OFFSET ?",
        (project_id, keep),
    )
    if row is None:
        return 0
    cur = execute(
        "DELETE FROM snapshots WHERE project_id=? AND kind != 'manual'"
        " AND created_at <= ?",
        (project_id, row["created_at"]),
    )
    return cur.rowcount or 0


# ------------------------------------------------------------------ diffing


def _section_texts(payload: dict[str, Any], *, zh: bool = False) -> dict[str, str]:
    """Flatten a payload to ``{"docid/key": text}`` for comparison."""
    field = "content_zh" if zh else "content"
    out: dict[str, str] = {}
    for doc_id, document in payload.items():
        for key, section in (document.get("sections") or {}).items():
            out[f"{doc_id}/{key}"] = section.get(field) or ""
    return out


def diff_snapshots(
    left_id: str, right_id: str, *, zh: bool = False, context: int = 3
) -> dict[str, Any]:
    """Unified diff between two snapshots, per section.

    ``right_id`` may be the literal ``"current"`` to diff a snapshot against the
    live database state - the common case for "what changed since the agent ran".
    """
    left = require(left_id)
    if right_id == "current":
        right_payload = capture_preview(left["project_id"])
        right_meta = {"id": "current", "label": "current state", "created_at": ""}
    else:
        right = require(right_id)
        right_payload = right["payload"]
        right_meta = {"id": right["id"], "label": right.get("label") or "",
                      "created_at": right.get("created_at") or ""}

    left_texts = _section_texts(left["payload"], zh=zh)
    right_texts = _section_texts(right_payload, zh=zh)
    keys = sorted(set(left_texts) | set(right_texts))

    sections: list[dict[str, Any]] = []
    added = removed = changed = 0
    for key in keys:
        before = left_texts.get(key, "")
        after = right_texts.get(key, "")
        if before == after:
            sections.append({
                "key": key, "status": "unchanged", "diff": "",
                "words_before": word_count(before), "words_after": word_count(after),
            })
            continue
        if not before:
            status = "added"
            added += 1
        elif not after:
            status = "removed"
            removed += 1
        else:
            status = "modified"
            changed += 1
        diff = "\n".join(
            difflib.unified_diff(
                before.splitlines(), after.splitlines(),
                fromfile=f"{key}@{left_id[:8]}", tofile=f"{key}@{right_meta['id'][:8]}",
                lineterm="", n=context,
            )
        )
        sections.append({
            "key": key, "status": status, "diff": diff,
            "words_before": word_count(before), "words_after": word_count(after),
        })
    return {
        "left": {"id": left["id"], "label": left.get("label") or "",
                 "created_at": left.get("created_at") or ""},
        "right": right_meta,
        "language": "zh" if zh else "primary",
        "summary": {"added": added, "removed": removed, "modified": changed,
                    "unchanged": len(keys) - added - removed - changed},
        "sections": sections,
    }


def capture_preview(project_id: str) -> dict[str, Any]:
    """Build a snapshot payload for the current state without persisting it."""
    payload: dict[str, Any] = {}
    for document in documents_store.list_documents(project_id):
        sections: dict[str, Any] = {}
        for section in documents_store.list_sections(document.id):
            sections[section.key] = {
                "title": section.title,
                "title_zh": section.title_zh,
                "content": section.content,
                "content_zh": section.content_zh,
                "status": section.status,
                "word_count": section.word_count,
                "ordering": section.ordering,
                "level": section.level,
            }
        payload[document.id] = {
            "title": document.title, "format": document.format,
            "rel_path": document.rel_path, "sections": sections,
        }
    return payload


def restore(snapshot_id: str, *, section_keys: list[str] | None = None) -> dict[str, Any]:
    """Write snapshot content back into the database.

    Destructive to current section text, so the caller (API route) takes a
    ``pre_restore`` snapshot first. ``section_keys`` restores a subset - the
    common "revert just the introduction" case.
    """
    snapshot = require(snapshot_id)
    wanted = set(section_keys) if section_keys else None
    restored, skipped = [], []
    for doc_id, document in (snapshot["payload"] or {}).items():
        if documents_store.get_document(doc_id, with_sections=False) is None:
            skipped.append(doc_id)
            continue
        for key, data in (document.get("sections") or {}).items():
            if wanted is not None and key not in wanted:
                continue
            section = documents_store.get_section_by_key(doc_id, key)
            if section is None:
                documents_store.create_section(
                    doc_id, key=key, title=data.get("title") or key,
                    title_zh=data.get("title_zh") or "",
                    ordering=int(data.get("ordering") or 0),
                    level=int(data.get("level") or 1),
                    content=data.get("content") or "",
                    content_zh=data.get("content_zh") or "",
                    status=data.get("status") or "drafted",
                )
            else:
                documents_store.update_section(
                    section.id,
                    content=data.get("content") or "",
                    content_zh=data.get("content_zh") or "",
                    title=data.get("title") or section.title,
                    status=data.get("status") or section.status,
                )
            restored.append(f"{doc_id}/{key}")
    return {"restored": restored, "skipped_documents": skipped,
            "snapshot_id": snapshot_id}

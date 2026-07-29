"""Skill registry mirror.

The **source of truth for a skill is its ``SKILL.md`` on disk** - that is what
the user edits, what git tracks, and what can be shared as a folder. This table
is a cache of the parsed frontmatter so listing skills does not require reading
and parsing every file, plus the fields that are *not* in the file: enabled
state and usage statistics.

``checksum`` is the hash of the on-disk file at import time. The loader compares
it on scan and re-parses when it differs, which is how external edits (or a git
pull) are picked up.
"""

from __future__ import annotations

from typing import Any

from ..core.db import dumps, execute, loads, query, query_one, row_to_dict
from ..core.errors import NotFoundError
from ..core.util import utc_now_iso


def upsert(record: dict[str, Any]) -> dict[str, Any]:
    """Insert or update one skill row, keyed by ``id``.

    ``enabled`` is preserved across re-imports: disabling a skill in the UI must
    survive the next filesystem scan.
    """
    now = utc_now_iso()
    existing = query_one("SELECT enabled, usage_count, created_at FROM skills WHERE id=?",
                         (record["id"],))
    enabled = (
        int(existing["enabled"]) if existing is not None
        else int(bool(record.get("enabled", True)))
    )
    usage = int(existing["usage_count"]) if existing is not None else 0
    created = existing["created_at"] if existing is not None else now
    execute(
        "INSERT INTO skills (id, name, version, scope, project_id, description,"
        " triggers, applies_to, tags, path, enabled, author, origin, checksum,"
        " usage_count, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(id) DO UPDATE SET name=excluded.name,"
        " version=excluded.version, scope=excluded.scope,"
        " project_id=excluded.project_id, description=excluded.description,"
        " triggers=excluded.triggers, applies_to=excluded.applies_to,"
        " tags=excluded.tags, path=excluded.path, author=excluded.author,"
        " origin=excluded.origin, checksum=excluded.checksum,"
        " updated_at=excluded.updated_at",
        (record["id"], record.get("name") or record["id"],
         record.get("version") or "0.1.0", record.get("scope") or "user",
         record.get("project_id") or None, record.get("description") or "",
         dumps(record.get("triggers") or []), dumps(record.get("applies_to") or []),
         dumps(record.get("tags") or []), record.get("path") or "", enabled,
         record.get("author") or "", record.get("origin") or "manual",
         record.get("checksum") or "", usage, created, now),
    )
    return require(record["id"])


def _decode(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["triggers"] = loads(item.get("triggers"), []) or []
    item["applies_to"] = loads(item.get("applies_to"), []) or []
    item["tags"] = loads(item.get("tags"), []) or []
    item["enabled"] = bool(item.get("enabled"))
    return item


def get(skill_id: str) -> dict[str, Any] | None:
    row = query_one("SELECT * FROM skills WHERE id=?", (skill_id,))
    return _decode(row) if row else None


def require(skill_id: str) -> dict[str, Any]:
    skill = get(skill_id)
    if skill is None:
        raise NotFoundError(f"skill {skill_id} not found")
    return skill


def list_skills(
    *, scope: str = "", project_id: str = "", enabled_only: bool = False
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if scope:
        clauses.append("scope=?")
        params.append(scope)
    if project_id:
        # Project-scoped skills plus globals, which apply everywhere.
        clauses.append("(project_id=? OR project_id IS NULL)")
        params.append(project_id)
    if enabled_only:
        clauses.append("enabled=1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = query(f"SELECT * FROM skills {where} ORDER BY scope, name", params)
    return [_decode(r) for r in rows]


def set_enabled(skill_id: str, enabled: bool) -> dict[str, Any]:
    require(skill_id)
    execute(
        "UPDATE skills SET enabled=?, updated_at=? WHERE id=?",
        (int(bool(enabled)), utc_now_iso(), skill_id),
    )
    return require(skill_id)


def record_use(skill_id: str) -> None:
    execute(
        "UPDATE skills SET usage_count=usage_count+1, last_used_at=? WHERE id=?",
        (utc_now_iso(), skill_id),
    )


def delete(skill_id: str) -> bool:
    """Remove the registry row only. File deletion is the loader's job so that
    the two operations can be reasoned about (and refused) independently."""
    cur = execute("DELETE FROM skills WHERE id=?", (skill_id,))
    return bool(cur.rowcount)


def known_ids() -> set[str]:
    return {r["id"] for r in query("SELECT id FROM skills")}


def prune_missing(present_ids: set[str]) -> int:
    """Drop rows whose skill directory no longer exists on disk."""
    stale = known_ids() - present_ids
    for skill_id in stale:
        execute("DELETE FROM skills WHERE id=?", (skill_id,))
    return len(stale)


def stats() -> dict[str, Any]:
    row = row_to_dict(
        query_one(
            "SELECT COUNT(*) AS total,"
            " COALESCE(SUM(enabled),0) AS enabled,"
            " COALESCE(SUM(usage_count),0) AS uses FROM skills"
        )
    )
    by_scope = query("SELECT scope, COUNT(*) AS n FROM skills GROUP BY scope")
    return {**(row or {}), "by_scope": {r["scope"]: r["n"] for r in by_scope}}

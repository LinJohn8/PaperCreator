"""Manuscript repository: documents, sections, and their on-disk mirror.

Disk <-> DB contract
--------------------
The **database is the source of truth** while the app runs: the editor, the
agents and the exporters all read and write ``sections.content``. Every write
also flushes a plain text file under ``<project>/manuscript/`` so that:

* git has something meaningful to diff and commit,
* the user can open the manuscript in any editor,
* the work survives a lost database (:func:`reindex_from_disk` reads it back).

One file per section, named ``NN-key.md`` (or ``.tex``), where ``NN`` is the
ordering. Bilingual content is written as a single file with an explicit
``<!-- zh -->`` separator rather than two files, so a translation can never
drift into a different section by accident.

Section keys are stable slugs (``abstract``, ``introduction``, ``method``) and
are what agents, templates and citations refer to. Titles are display-only.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from ..core.db import dumps, execute, loads, query, query_one, transaction
from ..core.errors import ConflictError, NotFoundError, ValidationError
from ..core.logging_setup import get_logger
from ..core.models import DocumentModel, SectionModel
from ..core.util import new_id, safe_filename, slugify, utc_now_iso, word_count
from . import projects as projects_store

log = get_logger(__name__)

ZH_SEPARATOR = "<!-- zh -->"
_FILENAME_RE = re.compile(r"^(\d{2,3})-(.+?)\.(md|tex)$")
SYNC_FORMAT = "papercreator-manuscript-sync"
SYNC_SCHEMA_VERSION = 2


def _row_to_section(row: Any) -> SectionModel:
    data = dict(row)
    return SectionModel(
        id=data["id"],
        document_id=data.get("document_id") or "",
        parent_id=data.get("parent_id"),
        key=data.get("key") or "",
        title=data.get("title") or "",
        title_zh=data.get("title_zh") or "",
        ordering=int(data.get("ordering") or 0),
        level=int(data.get("level") or 1),
        content=data.get("content") or "",
        content_zh=data.get("content_zh") or "",
        status=data.get("status") or "empty",
        target_words=int(data.get("target_words") or 0),
        target_words_zh=int(data.get("target_words_zh") or 0),
        word_count=int(data.get("word_count") or 0),
        guidance=data.get("guidance") or "",
        cited_paper_ids=loads(data.get("cited_paper_ids"), []) or [],
        meta=loads(data.get("meta"), {}) or {},
        created_at=data.get("created_at") or "",
        updated_at=data.get("updated_at") or "",
    )


def _row_to_document(row: Any) -> DocumentModel:
    data = dict(row)
    return DocumentModel(
        id=data["id"],
        project_id=data.get("project_id") or "",
        kind=data.get("kind") or "manuscript",
        title=data.get("title") or "",
        format=data.get("format") or "markdown",
        rel_path=data.get("rel_path") or "",
        created_at=data.get("created_at") or "",
        updated_at=data.get("updated_at") or "",
    )


# ------------------------------------------------------------------ documents


def create_document(
    project_id: str,
    *,
    title: str = "",
    kind: str = "manuscript",
    fmt: str = "markdown",
    rel_path: str = "",
) -> DocumentModel:
    projects_store.require(project_id)
    path = rel_path or ("manuscript" if kind == "manuscript" else f"{kind}s")
    if query_one(
        "SELECT id FROM documents WHERE project_id=? AND rel_path=?", (project_id, path)
    ):
        raise ConflictError(f"document at '{path}' already exists in this project")
    did = new_id("doc")
    now = utc_now_iso()
    execute(
        "INSERT INTO documents (id, project_id, kind, title, format, rel_path,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (did, project_id, kind, title, fmt, path, now, now),
    )
    return require_document(did)


def get_document(document_id: str, *, with_sections: bool = True) -> DocumentModel | None:
    row = query_one("SELECT * FROM documents WHERE id=?", (document_id,))
    if row is None:
        return None
    document = _row_to_document(row)
    if with_sections:
        document.sections = list_sections(document_id, nested=True)
        document.word_count = sum(
            s["word_count"] for s in _flat_section_rows(document_id)
        )
    return document


def require_document(document_id: str) -> DocumentModel:
    document = get_document(document_id)
    if document is None:
        raise NotFoundError(f"document {document_id} not found")
    return document


def list_documents(project_id: str, *, with_sections: bool = False) -> list[DocumentModel]:
    rows = query(
        "SELECT * FROM documents WHERE project_id=? ORDER BY created_at", (project_id,)
    )
    out = []
    for row in rows:
        document = _row_to_document(row)
        if with_sections:
            document.sections = list_sections(document.id, nested=True)
        document.word_count = sum(s["word_count"] for s in _flat_section_rows(document.id))
        out.append(document)
    return out


def primary_document(project_id: str) -> DocumentModel:
    """The project's manuscript, created on first access.

    Every project has exactly one ``kind='manuscript'`` document; notes and
    reviews are separate documents.
    """
    row = query_one(
        "SELECT * FROM documents WHERE project_id=? AND kind='manuscript'"
        " ORDER BY created_at LIMIT 1",
        (project_id,),
    )
    if row:
        return require_document(row["id"])
    project = projects_store.require(project_id)
    return create_document(
        project_id, title=project.title, kind="manuscript",
        fmt="markdown", rel_path="manuscript",
    )


def delete_document(document_id: str) -> bool:
    cur = execute("DELETE FROM documents WHERE id=?", (document_id,))
    return bool(cur.rowcount)


# ------------------------------------------------------------------- sections


def _flat_section_rows(document_id: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in query(
            "SELECT * FROM sections WHERE document_id=? ORDER BY ordering, created_at",
            (document_id,),
        )
    ]


def list_sections(document_id: str, *, nested: bool = False) -> list[SectionModel]:
    """Sections in document order. ``nested=True`` builds the parent/child tree."""
    sections = [_row_to_section(r) for r in _flat_section_rows(document_id)]
    if not nested:
        return sections
    by_id = {s.id: s for s in sections}
    roots: list[SectionModel] = []
    for section in sections:
        parent = by_id.get(section.parent_id) if section.parent_id else None
        if parent is not None and parent is not section:
            parent.children.append(section)
        else:
            roots.append(section)
    return roots


def get_section(section_id: str) -> SectionModel | None:
    row = query_one("SELECT * FROM sections WHERE id=?", (section_id,))
    return _row_to_section(row) if row else None


def require_section(section_id: str) -> SectionModel:
    section = get_section(section_id)
    if section is None:
        raise NotFoundError(f"section {section_id} not found")
    return section


def get_section_by_key(document_id: str, key: str) -> SectionModel | None:
    row = query_one(
        "SELECT * FROM sections WHERE document_id=? AND key=?", (document_id, key)
    )
    return _row_to_section(row) if row else None


def next_ordering(document_id: str) -> int:
    row = query_one(
        "SELECT COALESCE(MAX(ordering), 0) AS m FROM sections WHERE document_id=?",
        (document_id,),
    )
    return int(row["m"]) + 10 if row else 10


def create_section(
    document_id: str,
    *,
    key: str = "",
    title: str = "",
    title_zh: str = "",
    ordering: int | None = None,
    level: int = 1,
    parent_id: str | None = None,
    content: str = "",
    content_zh: str = "",
    guidance: str = "",
    target_words: int = 0,
    target_words_zh: int = 0,
    status: str = "empty",
    meta: dict[str, Any] | None = None,
) -> SectionModel:
    """Add a section. ``key`` is derived from the title when omitted."""
    require_document(document_id)
    resolved_key = key.strip() or slugify(title, fallback="section")
    if get_section_by_key(document_id, resolved_key):
        # Keys must be unique per document because agents address sections by
        # key; disambiguate rather than fail so template application is robust.
        suffix = 2
        while get_section_by_key(document_id, f"{resolved_key}-{suffix}"):
            suffix += 1
        resolved_key = f"{resolved_key}-{suffix}"
    sid = new_id("sec")
    now = utc_now_iso()
    execute(
        "INSERT INTO sections (id, document_id, parent_id, key, title, title_zh,"
        " ordering, level, content, content_zh, status, target_words, target_words_zh, word_count,"
        " guidance, cited_paper_ids, meta, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'[]',?,?,?)",
        (sid, document_id, parent_id, resolved_key, title or resolved_key.title(),
         title_zh, ordering if ordering is not None else next_ordering(document_id),
         level, content, content_zh, status, target_words, target_words_zh,
         word_count(content), guidance, dumps(meta or {}), now, now),
    )
    _touch_document(document_id)
    return require_section(sid)


def update_section(section_id: str, **fields: Any) -> SectionModel:
    """Patch a section. Recomputes ``word_count`` and status transitions.

    An explicit ``status`` in ``fields`` always wins; otherwise writing content
    into an ``empty`` section promotes it to ``drafted`` so the progress
    indicators reflect reality without the caller having to remember.
    """
    section = require_section(section_id)
    allowed = {
        "title", "title_zh", "content", "content_zh", "status", "guidance",
        "target_words", "target_words_zh", "level", "ordering", "parent_id",
    }
    assignments, params = [], {}
    for key, value in fields.items():
        if key in allowed:
            assignments.append(f"{key}=:{key}")
            params[key] = value
        elif key == "cited_paper_ids":
            assignments.append("cited_paper_ids=:cited_paper_ids")
            params["cited_paper_ids"] = dumps(value)
        elif key == "meta":
            assignments.append("meta=:meta")
            params["meta"] = dumps(value)
    if not assignments:
        return section

    if "content" in params:
        assignments.append("word_count=:word_count")
        params["word_count"] = word_count(str(params["content"]))
        if "status" not in params and section.status == "empty" and params["content"]:
            assignments.append("status=:status")
            params["status"] = "drafted"
    params["id"] = section_id
    params["updated_at"] = utc_now_iso()
    execute(
        f"UPDATE sections SET {','.join(assignments)}, updated_at=:updated_at"
        " WHERE id=:id",
        params,
    )
    _touch_document(section.document_id)
    return require_section(section_id)


def delete_section(section_id: str) -> bool:
    section = require_section(section_id)
    cur = execute("DELETE FROM sections WHERE id=?", (section_id,))
    _touch_document(section.document_id)
    return bool(cur.rowcount)


def reorder_sections(document_id: str, ordered_ids: list[str]) -> list[SectionModel]:
    """Rewrite ordering to match ``ordered_ids`` (step 10, leaving gaps)."""
    with transaction():
        for index, sid in enumerate(ordered_ids):
            execute(
                "UPDATE sections SET ordering=?, updated_at=? WHERE id=?"
                " AND document_id=?",
                ((index + 1) * 10, utc_now_iso(), sid, document_id),
            )
    _touch_document(document_id)
    return list_sections(document_id)


def _touch_document(document_id: str) -> None:
    now = utc_now_iso()
    execute("UPDATE documents SET updated_at=? WHERE id=?", (now, document_id))
    row = query_one("SELECT project_id FROM documents WHERE id=?", (document_id,))
    if row:
        projects_store.touch(row["project_id"])


# --------------------------------------------------------------- disk mirror


def document_dir(document: DocumentModel) -> Path:
    project = projects_store.require(document.project_id)
    return projects_store.project_root(project) / (document.rel_path or "manuscript")


def section_filename(section: SectionModel, fmt: str) -> str:
    ext = "tex" if fmt == "latex" else "md"
    return safe_filename(f"{section.ordering:03d}-{section.key}.{ext}")


def render_section_file(section: SectionModel, fmt: str) -> str:
    """Serialise one section to its on-disk representation.

    Heading level maps to ``#`` depth (markdown) or section/subsection (latex).
    The Chinese half follows :data:`ZH_SEPARATOR` so the parse is unambiguous.
    """
    lines: list[str] = []
    if fmt == "latex":
        command = {1: "section", 2: "subsection", 3: "subsubsection"}.get(
            section.level, "paragraph"
        )
        lines.append(f"\\{command}{{{section.title}}}")
        lines.append("")
        lines.append(section.content)
    else:
        lines.append(f"{'#' * max(1, min(6, section.level))} {section.title}")
        lines.append("")
        lines.append(section.content)
    if section.content_zh.strip():
        lines.extend(["", ZH_SEPARATOR, ""])
        if section.title_zh:
            if fmt == "latex":
                lines.append(f"% {section.title_zh}")
            else:
                lines.append(f"{'#' * max(1, min(6, section.level))} {section.title_zh}")
                lines.append("")
        lines.append(section.content_zh)
    return "\n".join(lines).rstrip() + "\n"


def _mirror_digest(entries: list[tuple[str, bytes]]) -> str:
    """Stable digest of managed filenames and bytes.

    Length prefixes avoid ambiguous concatenations. The digest contains no
    manuscript text and is safe to expose through the local status API.
    """
    digest = hashlib.sha256()
    for name, content in sorted(entries):
        encoded_name = name.encode("utf-8", errors="surrogatepass")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _database_mirror(document: DocumentModel) -> dict[str, Any]:
    entries: list[tuple[str, bytes]] = []
    for row in _flat_section_rows(document.id):
        section = _row_to_section(row)
        entries.append(
            (
                section_filename(section, document.format),
                render_section_file(section, document.format).encode("utf-8"),
            )
        )
    return {
        "digest": _mirror_digest(entries),
        "count": len(entries),
        "names": [name for name, _ in sorted(entries)],
        "entries": entries,
        "sections": _mirror_sections(entries),
    }


def _disk_mirror(document: DocumentModel) -> dict[str, Any]:
    directory = document_dir(document)
    entries: list[tuple[str, bytes]] = []
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if path.is_file() and _FILENAME_RE.match(path.name):
                entries.append((path.name, path.read_bytes()))
    return {
        "digest": _mirror_digest(entries),
        "count": len(entries),
        "names": [name for name, _ in entries],
        "entries": entries,
        "exists": directory.is_dir(),
        "sections": _mirror_sections(entries),
    }


def _mirror_sections(entries: list[tuple[str, bytes]]) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    duplicates: set[str] = set()
    for name, content in entries:
        match = _FILENAME_RE.match(name)
        if not match:
            continue
        key = match.group(2)
        if key in sections:
            duplicates.add(key)
            continue
        digest = hashlib.sha256()
        digest.update(name.encode("utf-8", errors="surrogatepass"))
        digest.update(b"\0")
        digest.update(content)
        sections[key] = {"filename": name, "fingerprint": digest.hexdigest()}
    for key in duplicates:
        sections[key]["duplicate"] = "true"
    return sections


def _sync_state_path(document: DocumentModel) -> Path:
    project = projects_store.require(document.project_id)
    return projects_store.project_root(project) / ".papercreator" / "manuscript-sync.json"


def _read_sync_state(document: DocumentModel) -> tuple[dict[str, Any], str]:
    path = _sync_state_path(document)
    if not path.is_file():
        return {
            "format": SYNC_FORMAT,
            "schema_version": SYNC_SCHEMA_VERSION,
            "documents": {},
        }, ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(
            payload.get("documents"), dict
        ):
            raise ValueError("documents must be an object")
        if payload.get("format") not in (None, SYNC_FORMAT):
            raise ValueError(f"unexpected format {payload.get('format')!r}")
        return payload, ""
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        # A broken baseline must make writes more conservative, never prevent
        # the manuscript from being read.
        log.warning("cannot read manuscript sync state %s: %s", path, exc)
        return {
            "format": SYNC_FORMAT,
            "schema_version": SYNC_SCHEMA_VERSION,
            "documents": {},
        }, str(exc)


def _record_sync(
    document: DocumentModel, database: dict[str, Any], disk: dict[str, Any]
) -> None:
    path = _sync_state_path(document)
    state, _error = _read_sync_state(document)
    state["format"] = SYNC_FORMAT
    state["schema_version"] = SYNC_SCHEMA_VERSION
    documents = state.setdefault("documents", {})
    documents[document.id] = {
        "db_digest": database["digest"],
        "disk_digest": disk["digest"],
        "db_sections": database["count"],
        "disk_files": disk["count"],
        "sections": {
            key: {
                "db_filename": database["sections"].get(key, {}).get("filename", ""),
                "db_fingerprint": database["sections"].get(key, {}).get("fingerprint", ""),
                "disk_filename": disk["sections"].get(key, {}).get("filename", ""),
                "disk_fingerprint": disk["sections"].get(key, {}).get("fingerprint", ""),
            }
            for key in sorted(set(database["sections"]) | set(disk["sections"]))
        },
        "synced_at": utc_now_iso(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def sync_status(document_id: str) -> dict[str, Any]:
    """Compare DB and disk against their last acknowledged common baseline."""
    document = require_document(document_id)
    database = _database_mirror(document)
    disk = _disk_mirror(document)
    state_file, state_error = _read_sync_state(document)
    baseline = (state_file.get("documents") or {}).get(document.id)
    baseline_present = isinstance(baseline, dict)
    baseline_sections = baseline.get("sections") if baseline_present else None
    section_baseline_present = isinstance(baseline_sections, dict)
    db_changed_keys: list[str] = []
    disk_changed_keys: list[str] = []
    conflicting_keys: list[str] = []
    merge_blockers: list[str] = []

    if section_baseline_present:
        all_keys = sorted(
            set(baseline_sections) | set(database["sections"]) | set(disk["sections"])
        )
        for key in all_keys:
            base = baseline_sections.get(key) or {}
            current_db = database["sections"].get(key) or {}
            current_disk = disk["sections"].get(key) or {}
            if current_db.get("fingerprint", "") != base.get("db_fingerprint", ""):
                db_changed_keys.append(key)
            if current_disk.get("fingerprint", "") != base.get("disk_fingerprint", ""):
                disk_changed_keys.append(key)
        conflicting_keys = sorted(set(db_changed_keys) & set(disk_changed_keys))
        changed_union = sorted(set(db_changed_keys) | set(disk_changed_keys))
        for key in changed_union:
            base = baseline_sections.get(key) or {}
            current_db = database["sections"].get(key) or {}
            current_disk = disk["sections"].get(key) or {}
            if not base.get("db_fingerprint") or not base.get("disk_fingerprint"):
                merge_blockers.append(f"{key}: no two-sided baseline")
            elif not current_db or not current_disk:
                merge_blockers.append(f"{key}: section added or removed")
            elif current_db.get("duplicate") or current_disk.get("duplicate"):
                merge_blockers.append(f"{key}: duplicate section files")
            elif (
                current_db.get("filename") != base.get("db_filename")
                or current_disk.get("filename") != base.get("disk_filename")
            ):
                merge_blockers.append(f"{key}: section filename changed")
    elif baseline_present:
        merge_blockers.append("the sync baseline predates per-section fingerprints")

    if baseline_present:
        db_changed = database["digest"] != baseline.get("db_digest")
        disk_changed = disk["digest"] != baseline.get("disk_digest")
        if db_changed and disk_changed:
            state = "diverged"
        elif db_changed:
            state = "database_changed"
        elif disk_changed:
            state = "disk_changed"
        else:
            state = "in_sync"
        can_flush = not disk_changed
        can_reindex = not db_changed
        synced_at = str(baseline.get("synced_at") or "")
    else:
        equivalent = database["digest"] == disk["digest"]
        db_changed = not equivalent and database["count"] > 0
        disk_changed = not equivalent and disk["count"] > 0
        if equivalent:
            state = "untracked_equal"
            can_flush = can_reindex = True
        elif disk["count"] == 0:
            state = "database_only"
            can_flush, can_reindex = True, False
        elif database["count"] == 0:
            state = "disk_only"
            can_flush, can_reindex = False, True
        else:
            state = "untracked_divergence"
            can_flush = can_reindex = False
        synced_at = ""

    can_auto_merge = bool(
        state == "diverged"
        and section_baseline_present
        and db_changed_keys
        and disk_changed_keys
        and not conflicting_keys
        and not merge_blockers
    )
    merge_token_payload = {
        "document_id": document.id,
        "baseline_db": baseline.get("db_digest") if baseline_present else None,
        "baseline_disk": baseline.get("disk_digest") if baseline_present else None,
        "database": database["digest"],
        "disk": disk["digest"],
        "db_changed_keys": db_changed_keys,
        "disk_changed_keys": disk_changed_keys,
        "conflicting_keys": conflicting_keys,
    }
    merge_preview_token = hashlib.sha256(
        json.dumps(
            merge_token_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    return {
        "document_id": document.id,
        "project_id": document.project_id,
        "path": str(document_dir(document)),
        "state_file": str(_sync_state_path(document)),
        "state": state,
        "baseline_present": baseline_present,
        "baseline_error": state_error,
        "db_changed": db_changed,
        "disk_changed": disk_changed,
        "can_flush": can_flush,
        "can_reindex": can_reindex,
        "synced_at": synced_at,
        "section_baseline_present": section_baseline_present,
        "section_changes": {
            "database": db_changed_keys,
            "disk": disk_changed_keys,
            "conflicts": conflicting_keys,
            "merge_blockers": merge_blockers,
        },
        "can_auto_merge": can_auto_merge,
        "merge_preview_token": merge_preview_token,
        "database": {
            "digest": database["digest"],
            "sections": database["count"],
            "files": database["names"],
        },
        "disk": {
            "digest": disk["digest"],
            "files_count": disk["count"],
            "files": disk["names"],
            "directory_exists": disk["exists"],
        },
    }


def merge_disjoint_changes(
    document_id: str, *, preview_token: str
) -> dict[str, Any]:
    if len(preview_token) != 64:
        raise ValidationError("a valid manuscript merge preview token is required")
    document = require_document(document_id)
    before = sync_status(document_id)
    if before["merge_preview_token"] != preview_token:
        raise ConflictError(
            "the manuscript changed after merge preview; review the conflict again",
            code="manuscript_merge_preview_stale",
            details={"sync": before},
        )
    if not before["can_auto_merge"]:
        raise ConflictError(
            "manuscript changes cannot be merged automatically because sections overlap, "
            "were added/removed/renamed, or lack a per-section baseline",
            code="manuscript_merge_not_disjoint",
            details={"sync": before},
        )

    database_backup = _backup_mirror(document, "database", before)
    disk_backup = _backup_mirror(document, "disk", before)
    disk = _disk_mirror(document)
    disk_entries = {
        match.group(2): content
        for name, content in disk["entries"]
        if (match := _FILENAME_RE.match(name))
    }
    with transaction():
        for key in before["section_changes"]["disk"]:
            existing = get_section_by_key(document_id, key)
            content = disk_entries.get(key)
            if existing is None or content is None:
                raise ConflictError(
                    f"section {key} changed during merge; review the conflict again",
                    code="manuscript_merge_preview_stale",
                )
            title, body, zh_body = parse_section_file(content.decode("utf-8"))
            update_section(
                existing.id,
                content=body,
                content_zh=zh_body,
                **({"title": title} if title else {}),
            )

    # The DB now contains both non-overlapping sets. A forced flush preserves
    # the pre-merge disk again and atomically re-establishes one mirror baseline.
    flushed = flush_document_to_disk(document_id, force=True)
    return {
        "merged_from_database": before["section_changes"]["database"],
        "merged_from_disk": before["section_changes"]["disk"],
        "sync_before": before,
        "sync": flushed["sync"],
        "safety_backups": [database_backup, disk_backup],
        "flush_backup": flushed.get("safety_backup"),
    }


def _backup_mirror(
    document: DocumentModel, side: str, status: dict[str, Any]
) -> dict[str, Any]:
    """Preserve the side a forced sync is about to overwrite."""
    project = projects_store.require(document.project_id)
    stamp = re.sub(r"[^0-9A-Za-z_-]", "-", utc_now_iso())
    root = (
        projects_store.project_root(project)
        / ".papercreator"
        / "conflicts"
        / f"{stamp}-{new_id('bak')}-{document.id}-{side}"
    )
    destination = root / side
    destination.mkdir(parents=True, exist_ok=False)
    names: list[str] = []
    if side == "disk":
        source = document_dir(document)
        if source.is_dir():
            for path in sorted(source.iterdir()):
                if path.is_file() and (
                    _FILENAME_RE.match(path.name) or path.name in ("full.md", "full.tex")
                ):
                    shutil.copy2(path, destination / path.name)
                    names.append(path.name)
    elif side == "database":
        for name, content in _database_mirror(document)["entries"]:
            (destination / name).write_bytes(content)
            names.append(name)
    else:
        raise ValueError(f"unknown sync side {side!r}")
    (root / "conflict.json").write_text(
        json.dumps(
            {
                "format": "papercreator-manuscript-conflict-backup",
                "schema_version": 1,
                "document_id": document.id,
                "project_id": document.project_id,
                "side": side,
                "created_at": utc_now_iso(),
                "files": names,
                "sync_status": status,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"path": str(root), "side": side, "files": names}


def backup_sync_side(document_id: str, side: str) -> dict[str, Any]:
    """Copy one manuscript mirror into the managed recovery area.

    Destructive workflows such as Git discard use this public wrapper before
    changing files.  The backup lives below the project's ``.papercreator``
    directory and carries the full sync-status evidence needed to understand
    why it was created.
    """
    if side not in ("database", "disk"):
        raise ValueError("side must be 'database' or 'disk'")
    document = require_document(document_id)
    return _backup_mirror(document, side, sync_status(document_id))


def _prepare_sync(document_id: str, direction: str, force: bool) -> tuple[
    DocumentModel, dict[str, Any], dict[str, Any] | None
]:
    document = require_document(document_id)
    status = sync_status(document_id)
    allowed = status["can_flush" if direction == "flush" else "can_reindex"]
    if allowed:
        return document, status, None
    losing_side = "disk" if direction == "flush" else "database"
    if not force:
        action = (
            "Use database" if direction == "flush" else "Use files"
        )
        raise ConflictError(
            (
                f"manuscript sync conflict: {losing_side} changed since the last "
                f"sync. Refusing to overwrite it. Review both sides, then choose "
                f"'{action}' explicitly."
            ),
            code="manuscript_sync_conflict",
            details={
                "direction": direction,
                "losing_side": losing_side,
                "sync": status,
                "resolution": {
                    "use_database": "POST flush?force=true",
                    "use_files": "POST reindex?force=true",
                },
            },
        )
    backup = _backup_mirror(document, losing_side, status)
    return document, status, backup


def ensure_sync_safe(document_id: str, direction: str) -> dict[str, Any]:
    """Preflight an operation that will later make DB or disk authoritative."""
    if direction not in ("flush", "reindex"):
        raise ValueError("direction must be 'flush' or 'reindex'")
    _document, status, _backup = _prepare_sync(document_id, direction, False)
    return status


def flush_document_to_disk(
    document_id: str, *, force: bool = False
) -> dict[str, Any]:
    """Write every section to ``<project>/<rel_path>/`` and prune stale files.

    Returns a summary for the API. Pruning only removes files matching the
    ``NNN-key.ext`` pattern this module generates, so user-added files in the
    manuscript directory are left alone.
    """
    document, before, safety_backup = _prepare_sync(document_id, "flush", force)
    target_dir = document_dir(document)
    target_dir.mkdir(parents=True, exist_ok=True)
    sections = [_row_to_section(r) for r in _flat_section_rows(document_id)]

    written: list[str] = []
    for section in sections:
        name = section_filename(section, document.format)
        (target_dir / name).write_text(
            render_section_file(section, document.format), encoding="utf-8"
        )
        written.append(name)

    removed: list[str] = []
    keep = set(written)
    for existing in target_dir.iterdir():
        if existing.is_file() and _FILENAME_RE.match(existing.name) and existing.name not in keep:
            existing.unlink()
            removed.append(existing.name)

    # A combined view, convenient for humans and for a quick git diff.
    combined = target_dir / ("full.tex" if document.format == "latex" else "full.md")
    combined.write_text(
        assemble_document_text(document_id, include_zh=False), encoding="utf-8"
    )
    database = _database_mirror(document)
    disk = _disk_mirror(document)
    _record_sync(document, database, disk)
    return {
        "path": str(target_dir), "written": written, "removed": removed,
        "sections": len(sections),
        "sync_before": before,
        "sync": sync_status(document_id),
        "safety_backup": safety_backup,
    }


def parse_section_file(text: str) -> tuple[str, str, str]:
    """Inverse of :func:`render_section_file`.

    Returns ``(title, content, content_zh)``. A missing heading yields an empty
    title and the whole body as content, so hand-written files still import.
    """
    head, sep, tail = text.partition(ZH_SEPARATOR)
    primary = head.strip("\n")
    zh_part = tail.strip("\n") if sep else ""

    title = ""
    body = primary
    lines = primary.splitlines()
    if lines:
        first = lines[0].strip()
        md_match = re.match(r"^#{1,6}\s+(.*)$", first)
        tex_match = re.match(r"^\\(?:sub){0,2}section\*?\{(.*)\}\s*$", first)
        if md_match:
            title = md_match.group(1).strip()
            body = "\n".join(lines[1:]).strip("\n")
        elif tex_match:
            title = tex_match.group(1).strip()
            body = "\n".join(lines[1:]).strip("\n")

    zh_body = zh_part
    zh_lines = zh_part.splitlines()
    if zh_lines:
        first_zh = zh_lines[0].strip()
        if re.match(r"^#{1,6}\s+", first_zh) or first_zh.startswith("%"):
            zh_body = "\n".join(zh_lines[1:]).strip("\n")
    return title, body, zh_body


def reindex_from_disk(
    document_id: str, *, force: bool = False
) -> dict[str, Any]:
    """Re-read the manuscript directory into the database.

    Used after a git checkout/pull, an external edit, or a database restore.
    Files win: this is an explicit "disk is now the truth" operation, so the
    caller must warn the user. Sections present in the DB but absent on disk
    are left untouched (never silently deleted).
    """
    document, before, safety_backup = _prepare_sync(document_id, "reindex", force)
    target_dir = document_dir(document)
    if not target_dir.is_dir():
        raise NotFoundError(f"manuscript directory '{target_dir}' does not exist")
    updated, created = 0, 0
    for path in sorted(target_dir.iterdir()):
        match = _FILENAME_RE.match(path.name) if path.is_file() else None
        if not match:
            continue
        ordering = int(match.group(1))
        key = match.group(2)
        title, body, zh_body = parse_section_file(path.read_text(encoding="utf-8"))
        existing = get_section_by_key(document_id, key)
        if existing is None:
            create_section(
                document_id, key=key, title=title or key.title(), ordering=ordering,
                content=body, content_zh=zh_body,
                status="drafted" if body.strip() else "empty",
            )
            created += 1
        else:
            update_section(
                existing.id, content=body, content_zh=zh_body, ordering=ordering,
                **({"title": title} if title else {}),
            )
            updated += 1
    log.info(
        "reindexed document %s from %s: %s created, %s updated",
        document_id, target_dir, created, updated,
    )
    refreshed = require_document(document_id)
    database = _database_mirror(refreshed)
    disk = _disk_mirror(refreshed)
    _record_sync(refreshed, database, disk)
    return {
        "created": created,
        "updated": updated,
        "path": str(target_dir),
        "sync_before": before,
        "sync": sync_status(document_id),
        "safety_backup": safety_backup,
    }


def assemble_document_text(
    document_id: str, *, include_zh: bool = False, only_keys: list[str] | None = None
) -> str:
    """Concatenate sections into one string (markdown or latex body).

    This is what the exporters, the critic agent and the word counter consume.
    """
    document = require_document(document_id)
    sections = [_row_to_section(r) for r in _flat_section_rows(document_id)]
    if only_keys:
        wanted = set(only_keys)
        sections = [s for s in sections if s.key in wanted]
    parts: list[str] = []
    for section in sections:
        if document.format == "latex":
            command = {1: "section", 2: "subsection", 3: "subsubsection"}.get(
                section.level, "paragraph"
            )
            parts.append(f"\\{command}{{{section.title}}}\n\n{section.content}".rstrip())
        else:
            heading = "#" * max(1, min(6, section.level))
            parts.append(f"{heading} {section.title}\n\n{section.content}".rstrip())
        if include_zh and section.content_zh.strip():
            parts.append(f"{ZH_SEPARATOR}\n\n{section.content_zh}".rstrip())
    return "\n\n".join(p for p in parts if p.strip()) + "\n"


def document_stats(document_id: str) -> dict[str, Any]:
    """Per-section progress, used by the status bar and the agent planner."""
    sections = [_row_to_section(r) for r in _flat_section_rows(document_id)]
    total_words = sum(s.word_count for s in sections)
    target = sum(s.target_words for s in sections)
    target_zh = sum(s.target_words_zh for s in sections)
    by_status: dict[str, int] = {}
    for section in sections:
        by_status[section.status] = by_status.get(section.status, 0) + 1
    return {
        "sections": len(sections),
        "words": total_words,
        "words_zh": sum(word_count(s.content_zh) for s in sections),
        "target_words": target,
        "target_words_zh": target_zh,
        "completion": round(total_words / target, 3) if target else 0.0,
        "completion_zh": round(
            sum(word_count(s.content_zh) for s in sections) / target_zh, 3
        ) if target_zh else 0.0,
        "by_status": by_status,
        "empty_sections": [s.key for s in sections if s.status == "empty"],
    }

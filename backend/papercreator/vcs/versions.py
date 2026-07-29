"""Unified version history across git commits and database snapshots.

Two version systems coexist, on purpose:

* **git commits** version the on-disk manuscript files. Durable, inspectable with
  any tool, survives a lost database.
* **snapshots** version the database state, including per-section metadata that
  the flattened files do not carry. Taken automatically around agent runs, so
  "what did the AI change?" is answerable without committing.

Presenting them as one timeline is what the user actually wants ("show me the
history of this paper"), so this module merges them and provides one comparison
interface that works whichever side an entry came from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.errors import NotFoundError, ValidationError
from ..core.logging_setup import get_logger
from ..store import documents as documents_store
from ..store import projects as projects_store
from ..store import snapshots as snapshots_store
from . import git as git_module

log = get_logger(__name__)


def project_repo(project_id: str) -> Path:
    project = projects_store.require(project_id)
    return projects_store.project_root(project)


def timeline(project_id: str, *, limit: int = 100) -> dict[str, Any]:
    """Merged, newest-first history of commits and snapshots."""
    directory = project_repo(project_id)
    entries: list[dict[str, Any]] = []

    for commit in git_module.log_entries(directory, limit=limit):
        entries.append({
            "kind": "commit",
            "id": commit["hash"],
            "short": commit["short"],
            "label": commit["subject"],
            "detail": commit["body"],
            "author": commit["author"],
            "timestamp": commit["date"],
            "auto": "[auto]" in commit["subject"],
        })

    for snapshot in snapshots_store.list_snapshots(project_id, limit=limit):
        stats = snapshot.get("stats") or {}
        entries.append({
            "kind": "snapshot",
            "id": snapshot["id"],
            "short": snapshot["id"][-8:],
            "label": snapshot.get("label") or snapshot.get("kind") or "snapshot",
            "detail": (
                f"{stats.get('sections', 0)} sections, {stats.get('words', 0)} words"
            ),
            "author": "",
            "timestamp": snapshot.get("created_at") or "",
            "snapshot_kind": snapshot.get("kind"),
            "git_commit": snapshot.get("git_commit") or "",
            "auto": snapshot.get("kind") in ("auto", "pre_agent", "post_agent"),
        })

    # ISO-8601 timestamps sort correctly as strings, and git's %aI and the
    # store's utc_now_iso() are both ISO-8601.
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    state = git_module.status(directory)
    return {
        "project_id": project_id,
        "path": str(directory),
        "git": {
            "is_repo": state.get("is_repo", False),
            "available": state.get("git_available", False),
            "branch": state.get("branch", ""),
            "clean": state.get("clean", True),
            "uncommitted": len(state.get("unstaged", []))
            + len(state.get("staged", []))
            + len(state.get("untracked", [])),
        },
        "entries": entries[:limit],
        "counts": {
            "commits": sum(1 for e in entries if e["kind"] == "commit"),
            "snapshots": sum(1 for e in entries if e["kind"] == "snapshot"),
        },
    }


def compare(
    project_id: str,
    left: str,
    right: str = "current",
    *,
    language: str = "primary",
    context: int = 3,
) -> dict[str, Any]:
    """Compare two versions, identified by commit hash or snapshot id.

    ``current`` on either side means the live state. Mixed comparisons (a commit
    against a snapshot) are not attempted - the two version worlds record
    different things, and a merged diff would be misleading. The response says so
    explicitly instead.
    """
    left_kind = _classify(project_id, left)
    right_kind = _classify(project_id, right)

    if left_kind == "snapshot" and right_kind in ("snapshot", "current"):
        return {
            "mode": "snapshot",
            **snapshots_store.diff_snapshots(
                left, right, zh=(language == "paired"), context=context
            ),
        }
    if left_kind == "commit" and right_kind in ("commit", "current"):
        directory = project_repo(project_id)
        ref = left if right_kind == "current" else f"{left}..{right}"
        result = git_module.diff(directory, ref=ref, context=context)
        return {
            "mode": "git",
            "left": {"id": left, "kind": "commit"},
            "right": {"id": right, "kind": right_kind},
            "diff": result["diff"],
            "stat": result["stat"],
            "note": "git diff of the on-disk manuscript files",
        }
    raise ValidationError(
        f"cannot compare a {left_kind} with a {right_kind}: git commits version the "
        f"files on disk while snapshots version the database state, so a combined "
        f"diff would be misleading. Compare two commits, or two snapshots.",
        details={"left": left_kind, "right": right_kind},
    )


def _classify(project_id: str, ref: str) -> str:
    if ref in ("current", "", "HEAD~0"):
        return "current"
    if ref.startswith("snp_") or snapshots_store.get(ref) is not None:
        return "snapshot"
    return "commit"


def save_version(
    project_id: str,
    *,
    label: str = "",
    commit_message: str = "",
    snapshot: bool = True,
    git_commit: bool = True,
) -> dict[str, Any]:
    """Create a version marker: a snapshot, a git commit, or both.

    Doing both is the default because they capture different things, and the
    snapshot records the commit hash so the two can be correlated later.
    """
    project = projects_store.require(project_id)
    directory = projects_store.project_root(project)
    result: dict[str, Any] = {"project_id": project_id, "label": label}

    # Flush the database to disk first, or the commit would capture stale files.
    document = documents_store.primary_document(project_id)
    flush = documents_store.flush_document_to_disk(document.id)
    result["files_written"] = len(flush.get("written", []))

    commit_hash = ""
    if git_commit and project.git_enabled and git_module.git_available():
        if not git_module.is_repo(directory):
            result["git_init"] = git_module.init_repo(directory)
        commit_result = git_module.commit(
            directory, commit_message or label or "Manuscript update"
        )
        result["git"] = commit_result
        commit_hash = commit_result.get("commit", "")
    elif git_commit:
        result["git"] = {
            "committed": False,
            "reason": (
                "git is not installed" if not git_module.git_available()
                else "git is disabled for this project"
            ),
        }

    if snapshot:
        result["snapshot"] = snapshots_store.capture(
            project_id, label=label or commit_message or "manual",
            kind="manual", git_commit=commit_hash,
        )
    return result


def restore_version(
    project_id: str,
    ref: str,
    *,
    section_keys: list[str] | None = None,
    take_snapshot: bool = True,
) -> dict[str, Any]:
    """Restore the manuscript to an earlier version.

    A ``pre_restore`` snapshot is taken first by default, so a restore is itself
    revertable - the operation is destructive to current text and the user may
    have picked the wrong version.
    """
    kind = _classify(project_id, ref)
    safety: dict[str, Any] = {}
    if take_snapshot:
        safety = snapshots_store.capture(
            project_id, label=f"before restoring {ref[:12]}", kind="manual"
        )

    if kind == "snapshot":
        document = documents_store.primary_document(project_id)
        documents_store.ensure_sync_safe(document.id, "flush")
        outcome = snapshots_store.restore(ref, section_keys=section_keys)
        documents_store.flush_document_to_disk(document.id)
        return {
            "mode": "snapshot", "restored": outcome, "safety_snapshot": safety,
            "note": "database sections were restored and written back to disk",
        }

    # Git restore: files first, then re-index them into the database.
    directory = project_repo(project_id)
    if not git_module.is_repo(directory):
        raise NotFoundError(
            f"'{directory}' is not a git repository, so commit {ref} cannot be "
            f"restored"
        )
    document = documents_store.primary_document(project_id)
    documents_store.ensure_sync_safe(document.id, "reindex")
    manuscript_rel = document.rel_path or "manuscript"
    git_module.restore_file(directory, manuscript_rel, ref=ref)
    reindex = documents_store.reindex_from_disk(document.id)
    return {
        "mode": "git",
        "ref": ref,
        "reindexed": reindex,
        "safety_snapshot": safety,
        "note": "manuscript files were restored from git and re-indexed into the "
                "database; sections present in the database but absent in that "
                "commit were left untouched",
    }


def file_history(project_id: str, section_key: str, *, limit: int = 30) -> dict[str, Any]:
    """History of one section: its git commits plus its snapshot versions.

    Answers "how has my introduction evolved?", which needs both sources - the
    file history from git and the per-section text from snapshots.
    """
    directory = project_repo(project_id)
    document = documents_store.primary_document(project_id)
    section = documents_store.get_section_by_key(document.id, section_key)
    if section is None:
        raise NotFoundError(f"section '{section_key}' does not exist")

    filename = documents_store.section_filename(section, document.format)
    rel_path = f"{document.rel_path or 'manuscript'}/{filename}"
    commits = git_module.log_entries(directory, limit=limit, path=rel_path)

    snapshot_versions: list[dict[str, Any]] = []
    for meta in snapshots_store.list_snapshots(project_id, limit=limit):
        full = snapshots_store.get(meta["id"])
        if full is None:
            continue
        payload = (full.get("payload") or {}).get(document.id) or {}
        stored = (payload.get("sections") or {}).get(section_key)
        if stored is None:
            continue
        snapshot_versions.append({
            "snapshot_id": meta["id"],
            "label": meta.get("label") or meta.get("kind"),
            "kind": meta.get("kind"),
            "timestamp": meta.get("created_at"),
            "words": stored.get("word_count", 0),
            "status": stored.get("status"),
            "preview": (stored.get("content") or "")[:300],
        })

    return {
        "section_key": section_key,
        "file": rel_path,
        "current_words": section.word_count,
        "current_status": section.status,
        "commits": commits,
        "snapshots": snapshot_versions,
    }


def section_at_version(
    project_id: str, section_key: str, ref: str
) -> dict[str, Any]:
    """One section's text at a given version, for side-by-side viewing."""
    document = documents_store.primary_document(project_id)
    section = documents_store.get_section_by_key(document.id, section_key)
    if section is None:
        raise NotFoundError(f"section '{section_key}' does not exist")

    if _classify(project_id, ref) == "snapshot":
        snapshot = snapshots_store.require(ref)
        payload = (snapshot.get("payload") or {}).get(document.id) or {}
        stored = (payload.get("sections") or {}).get(section_key) or {}
        return {
            "ref": ref, "kind": "snapshot",
            "content": stored.get("content", ""),
            "content_zh": stored.get("content_zh", ""),
            "title": stored.get("title", section.title),
            "found": bool(stored),
        }

    directory = project_repo(project_id)
    filename = documents_store.section_filename(section, document.format)
    rel_path = f"{document.rel_path or 'manuscript'}/{filename}"
    raw = git_module.show_file(directory, ref, rel_path)
    title, body, body_zh = documents_store.parse_section_file(raw) if raw else ("", "", "")
    return {
        "ref": ref, "kind": "commit", "content": body, "content_zh": body_zh,
        "title": title or section.title, "found": bool(raw), "file": rel_path,
    }

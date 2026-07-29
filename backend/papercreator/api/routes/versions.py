"""Version routes: git operations, snapshots, unified timeline, compare, restore.

Git commits and database snapshots are presented as one timeline, because "show
me the history of this paper" is the actual question. They are not mixed in a
diff, though - see :func:`papercreator.vcs.versions.compare` for why.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ...core.logging_setup import get_logger
from ...core.util import new_id, utc_now_iso
from ...store import projects as projects_store
from ...store import snapshots as snapshots_store
from ...vcs import git as git_module
from ...vcs import versions as versions_module

log = get_logger(__name__)
router = APIRouter(prefix="/api/versions", tags=["versions"])


@router.get("/{project_id}")
def timeline(project_id: str, limit: int = Query(100, le=500)) -> dict[str, Any]:
    """Merged commit + snapshot history, newest first."""
    return versions_module.timeline(project_id, limit=limit)


# ---------------------------------------------------------------------- git


@router.get("/{project_id}/git/status")
def git_status(project_id: str) -> dict[str, Any]:
    project = projects_store.require(project_id)
    return git_module.status(projects_store.project_root(project))


@router.post("/{project_id}/git/init")
def git_init(project_id: str) -> dict[str, Any]:
    project = projects_store.require(project_id)
    result = git_module.init_repo(projects_store.project_root(project))
    # Manual initialisation is an explicit opt-in. Persist it, otherwise the
    # later unified "Save version" route sees the stale False flag and silently
    # creates only a snapshot while leaving the Git worktree dirty.
    updated = projects_store.update(project_id, git_enabled=True)
    return {**result, "git_enabled": updated.git_enabled}


class CommitRequest(BaseModel):
    message: str
    paths: list[str] = Field(default_factory=list)
    flush_first: bool = True


@router.post("/{project_id}/git/commit")
def git_commit(project_id: str, request: CommitRequest) -> dict[str, Any]:
    """Commit the manuscript.

    ``flush_first`` writes the database's section text to disk before staging -
    without it a commit would capture the previous save, which is the most likely
    way to produce a confusing empty diff.
    """
    from ...store import documents as documents_store

    project = projects_store.require(project_id)
    directory = projects_store.project_root(project)
    result: dict[str, Any] = {}
    if request.flush_first:
        document = documents_store.primary_document(project_id)
        result["flushed"] = documents_store.flush_document_to_disk(document.id)
    result["commit"] = git_module.commit(
        directory, request.message, paths=request.paths or None
    )
    return result


@router.get("/{project_id}/git/log")
def git_log(
    project_id: str, limit: int = Query(50, le=500), path: str = ""
) -> dict[str, Any]:
    project = projects_store.require(project_id)
    return {
        "entries": git_module.log_entries(
            projects_store.project_root(project), limit=limit, path=path
        )
    }


@router.get("/{project_id}/git/diff")
def git_diff(
    project_id: str, ref: str = "", path: str = "", staged: bool = False
) -> dict[str, Any]:
    project = projects_store.require(project_id)
    return git_module.diff(
        projects_store.project_root(project), ref=ref, path=path, staged=staged
    )


@router.get("/{project_id}/git/branches")
def git_branches(project_id: str) -> dict[str, Any]:
    project = projects_store.require(project_id)
    return git_module.list_branches(projects_store.project_root(project))


class BranchRequest(BaseModel):
    name: str
    checkout: bool = True


@router.post("/{project_id}/git/branch")
def git_branch(project_id: str, request: BranchRequest) -> dict[str, Any]:
    """Create a branch - the safe way to try a risky rewrite."""
    project = projects_store.require(project_id)
    return git_module.create_branch(
        projects_store.project_root(project), request.name,
        checkout=request.checkout,
    )


class CheckoutRequest(BaseModel):
    ref: str


@router.post("/{project_id}/git/checkout")
def git_checkout(project_id: str, request: CheckoutRequest) -> dict[str, Any]:
    """Switch branches, then re-index the manuscript files into the database.

    The re-index is essential: without it the editor would keep showing the old
    branch's text while the files on disk are the new branch's.
    """
    from ...store import documents as documents_store

    project = projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    documents_store.ensure_sync_safe(document.id, "reindex")
    safety = snapshots_store.capture(
        project_id, label=f"before checkout {request.ref[:24]}", kind="manual"
    )
    result = git_module.checkout(projects_store.project_root(project), request.ref)
    result["reindexed"] = documents_store.reindex_from_disk(document.id)
    result["safety_snapshot"] = safety
    return result


class RemoteRequest(BaseModel):
    url: str
    name: str = "origin"


@router.get("/{project_id}/git/remotes")
def git_remotes(project_id: str) -> dict[str, Any]:
    project = projects_store.require(project_id)
    return git_module.remote_info(projects_store.project_root(project))


@router.post("/{project_id}/git/remote")
def git_set_remote(project_id: str, request: RemoteRequest) -> dict[str, Any]:
    project = projects_store.require(project_id)
    return git_module.set_remote(
        projects_store.project_root(project), request.url, name=request.name
    )


@router.delete("/{project_id}/git/remote")
def git_remove_remote(
    project_id: str, name: str = Query("origin")
) -> dict[str, Any]:
    """Disconnect a remote while preserving every local commit and branch."""
    project = projects_store.require(project_id)
    return git_module.remove_remote(
        projects_store.project_root(project), name=name
    )


@router.post("/{project_id}/git/fetch")
def git_fetch(
    project_id: str, remote: str = Query("origin")
) -> dict[str, Any]:
    """Refresh remote-tracking refs without changing manuscript files."""
    project = projects_store.require(project_id)
    return git_module.fetch(projects_store.project_root(project), remote=remote)


@router.post("/{project_id}/git/pull")
def git_pull(
    project_id: str, remote: str = Query("origin")
) -> dict[str, Any]:
    """Fetch and apply a remote update only when it is a clean fast-forward.

    A diverged history is intentionally returned as a conflict instead of
    running an automatic merge. Before a real file update, the database mirror
    must be safe to replace and PaperCreator captures both a database snapshot
    and the current disk manuscript. The updated files are then re-indexed.
    """
    from ...store import documents as documents_store

    project = projects_store.require(project_id)
    directory = projects_store.project_root(project)
    fetched = git_module.fetch(directory, remote=remote)
    sync = fetched["sync"]

    # Let the Git safety layer return precise unpublished/ahead/diverged errors
    # without creating misleading recovery artifacts when no file can change.
    if not sync.get("can_fast_forward"):
        pulled = git_module.fast_forward(directory, remote=remote)
        return {**pulled, "fetched": fetched}

    document = documents_store.primary_document(project_id)
    documents_store.ensure_sync_safe(document.id, "reindex")
    safety = snapshots_store.capture(
        project_id, label=f"before pulling {remote}", kind="manual"
    )
    manuscript_backup = documents_store.backup_sync_side(document.id, "disk")
    pulled = git_module.fast_forward(directory, remote=remote)
    pulled["reindexed"] = documents_store.reindex_from_disk(document.id)
    pulled["safety_snapshot"] = safety
    pulled["manuscript_backup"] = manuscript_backup
    pulled["fetched"] = fetched
    return pulled


@router.post("/{project_id}/git/push")
def git_push(
    project_id: str, remote: str = Query("origin"), branch: str = Query("")
) -> dict[str, Any]:
    """Push to a remote. Never forces.

    Requires a configured credential helper: this backend cannot prompt, so an
    unauthenticated push fails immediately with an explanation instead of hanging.
    """
    project = projects_store.require(project_id)
    return git_module.push(
        projects_store.project_root(project), remote=remote, branch=branch
    )


@router.post("/{project_id}/git/discard")
def git_discard(
    project_id: str,
    confirm: bool = Query(False, description="required; the operation is irreversible"),
) -> dict[str, Any]:
    """Discard tracked changes, preserving recovery data and DB consistency.

    A bare call still only returns the confirmation conflict.  Once confirmed,
    the database must be safe to replace from files; then a snapshot, manuscript
    mirror and binary Git patch are captured below the project metadata before
    Git changes anything.  The post-discard files are finally re-indexed so the
    editor cannot keep showing the discarded text.
    """
    project = projects_store.require(project_id)
    directory = projects_store.project_root(project)
    if not confirm:
        return git_module.discard_changes(directory, confirm=False)

    from ...store import documents as documents_store

    document = documents_store.primary_document(project_id)
    documents_store.ensure_sync_safe(document.id, "reindex")
    snapshot = snapshots_store.capture(
        project_id, label="before discarding Git changes", kind="manual"
    )
    manuscript_backup = documents_store.backup_sync_side(document.id, "disk")
    stamp = re.sub(r"[^0-9A-Za-z_-]", "-", utc_now_iso())
    recovery_dir = (
        directory / ".papercreator" / "conflicts"
        / f"{stamp}-{new_id('bak')}-git-discard"
    )
    patch = git_module.backup_worktree_patch(
        directory, recovery_dir / "tracked-changes.patch"
    )
    discarded = git_module.discard_changes(directory, confirm=True)
    reindexed = documents_store.reindex_from_disk(document.id)
    return {
        **discarded,
        "reindexed": reindexed,
        "safety_snapshot": snapshot,
        "manuscript_backup": manuscript_backup,
        "git_patch": patch,
    }


# ----------------------------------------------------------------- snapshots


@router.get("/{project_id}/snapshots")
def list_snapshots(project_id: str, limit: int = Query(100, le=500)) -> dict[str, Any]:
    projects_store.require(project_id)
    return {"items": snapshots_store.list_snapshots(project_id, limit=limit)}


class SnapshotRequest(BaseModel):
    label: str = ""
    kind: str = "manual"


@router.post("/{project_id}/snapshots")
def create_snapshot(project_id: str, request: SnapshotRequest) -> dict[str, Any]:
    projects_store.require(project_id)
    return snapshots_store.capture(
        project_id, label=request.label, kind=request.kind
    )


@router.get("/{project_id}/snapshots/{snapshot_id}")
def get_snapshot(project_id: str, snapshot_id: str) -> dict[str, Any]:
    projects_store.require(project_id)
    return snapshots_store.require(snapshot_id)


@router.delete("/{project_id}/snapshots/{snapshot_id}")
def delete_snapshot(project_id: str, snapshot_id: str) -> dict[str, Any]:
    projects_store.require(project_id)
    return {"deleted": snapshots_store.delete(snapshot_id)}


@router.post("/{project_id}/snapshots/prune")
def prune_snapshots(project_id: str, keep: int = Query(50, ge=5)) -> dict[str, Any]:
    """Drop old automatic snapshots. Manually labelled ones are never pruned."""
    projects_store.require(project_id)
    return {
        "removed": snapshots_store.prune(project_id, keep=keep),
        "note": "manual snapshots were preserved",
    }


# ------------------------------------------------------------ unified version


class SaveVersionRequest(BaseModel):
    label: str = ""
    commit_message: str = ""
    snapshot: bool = True
    git_commit: bool = True


@router.post("/{project_id}/save")
def save_version(project_id: str, request: SaveVersionRequest) -> dict[str, Any]:
    """Mark a version: a snapshot, a git commit, or both.

    Both by default - they capture different things, and the snapshot records the
    commit hash so the two can be correlated afterwards.
    """
    return versions_module.save_version(
        project_id,
        label=request.label,
        commit_message=request.commit_message,
        snapshot=request.snapshot,
        git_commit=request.git_commit,
    )


@router.get("/{project_id}/compare")
def compare(
    project_id: str,
    left: str = Query(..., description="commit hash or snapshot id"),
    right: str = Query("current", description="commit hash, snapshot id, or 'current'"),
    language: str = Query("primary", pattern="^(primary|paired)$"),
    context: int = Query(3, ge=0, le=20),
) -> dict[str, Any]:
    """Diff two versions. Both sides must be the same kind (commits or snapshots)."""
    return versions_module.compare(
        project_id, left, right, language=language, context=context
    )


class RestoreRequest(BaseModel):
    ref: str
    section_keys: list[str] = Field(default_factory=list)
    take_snapshot: bool = True


@router.post("/{project_id}/restore")
def restore(project_id: str, request: RestoreRequest) -> dict[str, Any]:
    """Restore the manuscript to an earlier version.

    A safety snapshot is taken first by default, so the restore is itself
    revertable. ``section_keys`` restores a subset - the "revert just the
    introduction" case.
    """
    return versions_module.restore_version(
        project_id, request.ref,
        section_keys=request.section_keys or None,
        take_snapshot=request.take_snapshot,
    )


@router.get("/{project_id}/sections/{section_key}/history")
def section_history(
    project_id: str, section_key: str, limit: int = Query(30, le=200)
) -> dict[str, Any]:
    """One section's evolution, from both git and snapshots."""
    return versions_module.file_history(project_id, section_key, limit=limit)


@router.get("/{project_id}/sections/{section_key}/at")
def section_at(
    project_id: str, section_key: str, ref: str = Query(...)
) -> dict[str, Any]:
    """A section's text at a given version, for side-by-side comparison."""
    return versions_module.section_at_version(project_id, section_key, ref)

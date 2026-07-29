"""Skill discovery, import, and CRUD on disk.

Three sources, in increasing precedence:

1. **builtin** - shipped with the app under ``resources/skills/``. Read-only;
   editing one copies it to the user directory first.
2. **user** - ``<home>/skills/``. Authored by hand, through the UI, or by an LLM.
3. **project** - ``<project>/.papercreator/skills/``. Travels with the project's
   git repo, so a collaborator gets the project's conventions automatically.

Discovery is a filesystem scan reconciled into the ``skills`` table
(:mod:`store.skills_store`). The files stay authoritative; the table caches
metadata and holds the enabled flag and usage counters, which do not belong in a
git-tracked file.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..core.errors import ConflictError, NotFoundError, ValidationError
from ..core.logging_setup import get_logger
from ..core.paths import get_paths
from ..core.util import slugify
from ..store import projects as projects_store
from ..store import skills_store
from .model import SKILL_FILENAME, Skill, load_skill_dir, render_skill_file

log = get_logger(__name__)


def builtin_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "resources" / "skills"


def user_dir() -> Path:
    directory = get_paths().user_skills_dir
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def project_dir(project_id: str) -> Path:
    project = projects_store.require(project_id)
    return projects_store.project_root(project) / ".papercreator" / "skills"


def _scan(directory: Path, scope: str, project_id: str = "") -> list[Skill]:
    if not directory.is_dir():
        return []
    found: list[Skill] = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if not (entry / SKILL_FILENAME).is_file():
            continue
        try:
            found.append(load_skill_dir(entry, scope=scope, project_id=project_id))
        except (ValidationError, OSError, UnicodeDecodeError) as exc:
            # One malformed skill must not hide the rest.
            log.warning("skipping skill at %s: %s", entry, exc)
    return found


def discover(*, project_id: str = "") -> list[Skill]:
    """All skills visible right now, later scopes overriding earlier by id.

    Override order (user beats builtin, project beats user) lets the user
    customise a shipped skill by creating one with the same id, which is the
    least surprising behaviour and needs no separate "fork" concept.
    """
    by_id: dict[str, Skill] = {}
    for skill in _scan(builtin_dir(), "builtin"):
        by_id[skill.id] = skill
    for skill in _scan(user_dir(), "user"):
        by_id[skill.id] = skill
    if project_id:
        for skill in _scan(project_dir(project_id), "project", project_id):
            by_id[skill.id] = skill
    return sorted(by_id.values(), key=lambda s: (s.priority, s.name))


def sync_registry(*, project_id: str = "") -> dict[str, Any]:
    """Reconcile the filesystem into the ``skills`` table.

    Called at startup and after any skill write. Rows whose directory has
    disappeared are pruned; the ``enabled`` flag is preserved across syncs by
    :func:`store.skills_store.upsert`.
    """
    found = discover(project_id=project_id)
    for skill in found:
        skills_store.upsert(skill.to_record())
    pruned = skills_store.prune_missing({s.id for s in found})
    log.info("skill registry synced: %s present, %s pruned", len(found), pruned)
    return {
        "count": len(found),
        "pruned": pruned,
        "by_scope": {
            scope: sum(1 for s in found if s.scope == scope)
            for scope in ("builtin", "user", "project")
        },
    }


def get_skill(skill_id: str, *, project_id: str = "") -> Skill:
    """Load one skill from disk by id."""
    for skill in discover(project_id=project_id):
        if skill.id == skill_id:
            return skill
    raise NotFoundError(f"skill '{skill_id}' not found")


def load_many(skill_ids: list[str], *, project_id: str = "") -> tuple[list[Skill], list[str]]:
    """Load several skills. Returns ``(skills, problems)``.

    Missing or unparseable skills are reported rather than raised, because a
    broken skill should degrade an agent run, not abort it.
    """
    available = {s.id: s for s in discover(project_id=project_id)}
    loaded: list[Skill] = []
    problems: list[str] = []
    for skill_id in skill_ids:
        skill = available.get(skill_id)
        if skill is None:
            problems.append(f"skill '{skill_id}' not found and was skipped")
            continue
        row = skills_store.get(skill_id)
        if row is not None and not row.get("enabled", True):
            problems.append(f"skill '{skill_id}' is disabled and was skipped")
            continue
        loaded.append(skill)
    return loaded, problems


def _target_dir(scope: str, project_id: str = "") -> Path:
    if scope == "project":
        if not project_id:
            raise ValidationError("a project-scoped skill needs a project_id")
        directory = project_dir(project_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory
    if scope == "builtin":
        raise ConflictError(
            "builtin skills are read-only. Save it as a user skill instead - use "
            "the same id to override the builtin."
        )
    return user_dir()


def save_skill(
    *,
    name: str,
    instructions: str,
    skill_id: str = "",
    description: str = "",
    applies_to: list[str] | None = None,
    triggers: list[str] | None = None,
    tags: list[str] | None = None,
    author: str = "",
    origin: str = "manual",
    scope: str = "user",
    project_id: str = "",
    version: str = "0.1.0",
    priority: int = 50,
    overwrite: bool = False,
) -> Skill:
    """Create or update a skill on disk, then sync the registry."""
    if not name.strip():
        raise ValidationError("a skill needs a name")
    if not instructions.strip():
        raise ValidationError("a skill needs instructions")

    resolved_id = slugify(skill_id or name, fallback="skill")
    directory = _target_dir(scope, project_id) / resolved_id
    skill_file = directory / SKILL_FILENAME
    if skill_file.exists() and not overwrite:
        raise ConflictError(
            f"skill '{resolved_id}' already exists at {directory}. Pass "
            f"overwrite=true to replace it.",
            details={"skill_id": resolved_id, "path": str(directory)},
        )

    skill = Skill(
        id=resolved_id,
        name=name.strip(),
        description=description.strip(),
        version=version,
        author=author.strip(),
        scope=scope,
        project_id=project_id,
        instructions=instructions.strip(),
        triggers=triggers or [],
        applies_to=applies_to or ["all"],
        tags=tags or [],
        priority=priority,
        origin=origin,
        path=str(directory),
    )
    directory.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(render_skill_file(skill), encoding="utf-8")
    # Re-read so the stored checksum and parse match exactly what is on disk.
    written = load_skill_dir(directory, scope=scope, project_id=project_id)
    skills_store.upsert(written.to_record())
    log.info("saved skill '%s' (%s) at %s", written.id, scope, directory)
    return written


def delete_skill(skill_id: str, *, project_id: str = "") -> dict[str, Any]:
    """Delete a user or project skill's folder and its registry row.

    Builtins cannot be deleted; a user override of a builtin can, which restores
    the builtin on the next scan.
    """
    skill = get_skill(skill_id, project_id=project_id)
    if skill.scope == "builtin":
        raise ConflictError(
            f"'{skill_id}' is a builtin skill and cannot be deleted. Disable it "
            f"instead."
        )
    directory = Path(skill.path)
    removed = False
    if directory.is_dir():
        # Guard: only ever delete inside a known skills directory.
        allowed_roots = [user_dir().resolve()]
        if project_id:
            allowed_roots.append(project_dir(project_id).resolve())
        try:
            inside = any(
                directory.resolve().is_relative_to(root) for root in allowed_roots
            )
        except (OSError, ValueError):
            inside = False
        if not inside:
            raise ConflictError(
                f"refusing to delete '{directory}': it is outside the skills "
                f"directories"
            )
        shutil.rmtree(directory, ignore_errors=True)
        removed = True
    skills_store.delete(skill_id)
    log.info("deleted skill '%s' (files removed: %s)", skill_id, removed)
    return {"deleted": True, "files_removed": removed, "path": str(directory)}


def copy_to_user(skill_id: str, *, new_id: str = "") -> Skill:
    """Copy a builtin (or project) skill into the user directory for editing."""
    source = get_skill(skill_id)
    target_id = slugify(new_id or f"{skill_id}", fallback="skill")
    target = user_dir() / target_id
    if target.exists():
        raise ConflictError(f"user skill '{target_id}' already exists")
    shutil.copytree(Path(source.path), target)
    skill = load_skill_dir(target, scope="user")
    skills_store.upsert(skill.to_record())
    return skill


def import_from_path(path: str, *, scope: str = "user", project_id: str = "") -> Skill:
    """Import a skill folder from anywhere on disk (shared by a colleague)."""
    source = Path(path).expanduser().resolve()
    if not (source / SKILL_FILENAME).is_file():
        raise ValidationError(f"'{source}' contains no {SKILL_FILENAME}")
    parsed = load_skill_dir(source, scope=scope, project_id=project_id)
    target = _target_dir(scope, project_id) / parsed.id
    if target.exists():
        raise ConflictError(f"skill '{parsed.id}' already exists at {target}")
    shutil.copytree(source, target)
    skill = load_skill_dir(target, scope=scope, project_id=project_id)
    skill.origin = "imported"
    skills_store.upsert(skill.to_record())
    log.info("imported skill '%s' from %s", skill.id, source)
    return skill


def list_with_status(*, project_id: str = "") -> list[dict[str, Any]]:
    """Skills plus their registry state, for the UI list."""
    out: list[dict[str, Any]] = []
    for skill in discover(project_id=project_id):
        row = skills_store.get(skill.id) or {}
        out.append({
            "id": skill.id,
            "name": skill.name,
            "description": skill.description,
            "version": skill.version,
            "scope": skill.scope,
            "author": skill.author,
            "origin": skill.origin,
            "applies_to": skill.applies_to,
            "triggers": skill.triggers,
            "tags": skill.tags,
            "priority": skill.priority,
            "path": skill.path,
            "enabled": bool(row.get("enabled", True)),
            "usage_count": int(row.get("usage_count") or 0),
            "last_used_at": row.get("last_used_at"),
            "editable": skill.scope != "builtin",
            "instruction_chars": len(skill.instructions),
            "has_examples": bool(skill.examples),
            "checksum_matches": row.get("checksum") == skill.checksum
            if row else None,
        })
    return out

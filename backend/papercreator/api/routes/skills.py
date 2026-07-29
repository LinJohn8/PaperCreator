"""Skill routes: list, author, edit, preview.

LLM authoring is deliberately two steps - ``POST /draft`` returns a proposal, and
the user posts it back to ``POST /`` to save. A skill silently altering every
future prompt is exactly the kind of change that should be reviewed first.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ...core.errors import ValidationError
from ...core.logging_setup import get_logger
from ...skills import loader, runner
from ...store import skills_store

log = get_logger(__name__)
router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.get("")
def list_skills(project_id: str = "") -> dict[str, Any]:
    """All visible skills with their enabled state and usage counts."""
    return {
        "items": loader.list_with_status(project_id=project_id),
        "stats": skills_store.stats(),
        "directories": {
            "builtin": str(loader.builtin_dir()),
            "user": str(loader.user_dir()),
        },
        "valid_roles": sorted(
            __import__("papercreator.skills.model", fromlist=["VALID_TARGETS"])
            .VALID_TARGETS
        ),
    }


@router.post("/sync")
def sync_registry(project_id: str = "") -> dict[str, Any]:
    """Rescan the skill directories. Picks up hand edits and git pulls."""
    return loader.sync_registry(project_id=project_id)


@router.get("/{skill_id}")
def get_skill(skill_id: str, project_id: str = "") -> dict[str, Any]:
    """One skill, including its full instruction text."""
    skill = loader.get_skill(skill_id, project_id=project_id)
    row = skills_store.get(skill_id) or {}
    return {
        **{k: v for k, v in skill.__dict__.items()},
        "enabled": bool(row.get("enabled", True)),
        "usage_count": int(row.get("usage_count") or 0),
        "last_used_at": row.get("last_used_at"),
        "editable": skill.scope != "builtin",
    }


class SkillSave(BaseModel):
    name: str
    instructions: str
    id: str = ""
    description: str = ""
    applies_to: list[str] = Field(default_factory=lambda: ["all"])
    triggers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    author: str = ""
    origin: str = "manual"
    scope: str = "user"
    project_id: str = ""
    version: str = "0.1.0"
    priority: int = 50
    overwrite: bool = False


@router.post("")
def save_skill(request: SkillSave) -> dict[str, Any]:
    """Create or update a skill on disk.

    Builtins are read-only; saving with a builtin's id under ``user`` scope
    overrides it, which is the intended way to customise a shipped skill.
    """
    skill = loader.save_skill(
        name=request.name,
        instructions=request.instructions,
        skill_id=request.id,
        description=request.description,
        applies_to=request.applies_to,
        triggers=request.triggers,
        tags=request.tags,
        author=request.author,
        origin=request.origin,
        scope=request.scope,
        project_id=request.project_id,
        version=request.version,
        priority=request.priority,
        overwrite=request.overwrite,
    )
    return {"skill": {k: v for k, v in skill.__dict__.items()}}


class DraftRequest(BaseModel):
    request: str
    existing_skill_id: str = ""
    project_id: str = ""
    model: str = ""


@router.post("/draft")
async def draft_skill(body: DraftRequest) -> dict[str, Any]:
    """Ask the LLM to write a skill. Returns a draft; does not save it.

    This is the "add a skill by talking to the model" path. The draft is validated
    (role names checked, size warned about) so the user reviews something already
    known to be well-formed.
    """
    draft = await runner.draft_skill_with_llm(
        body.request,
        existing_skill_id=body.existing_skill_id,
        project_id=body.project_id,
        model=body.model,
    )
    return {
        "draft": draft,
        "next_step": "review the draft, then POST it to /api/skills to save it",
    }


@router.post("/{skill_id}/enabled")
def set_enabled(skill_id: str, enabled: bool = Query(...)) -> dict[str, Any]:
    """Enable or disable a skill. Survives filesystem rescans."""
    return {"skill": skills_store.set_enabled(skill_id, enabled)}


@router.delete("/{skill_id}")
def delete_skill(skill_id: str, project_id: str = "") -> dict[str, Any]:
    """Delete a user or project skill. Builtins can only be disabled."""
    return loader.delete_skill(skill_id, project_id=project_id)


class CopyRequest(BaseModel):
    new_id: str = ""


@router.post("/{skill_id}/copy")
def copy_skill(skill_id: str, request: CopyRequest) -> dict[str, Any]:
    """Copy a builtin into the user directory so it can be edited."""
    skill = loader.copy_to_user(skill_id, new_id=request.new_id)
    return {"skill": {k: v for k, v in skill.__dict__.items()}}


class ImportRequest(BaseModel):
    path: str
    scope: str = "user"
    project_id: str = ""


@router.post("/import")
def import_skill(request: ImportRequest) -> dict[str, Any]:
    """Import a skill folder shared by someone else."""
    skill = loader.import_from_path(
        request.path, scope=request.scope, project_id=request.project_id
    )
    return {"skill": {k: v for k, v in skill.__dict__.items()}}


class PreviewRequest(BaseModel):
    skill_ids: list[str]
    role: str = ""
    project_id: str = ""


@router.post("/preview")
def preview(request: PreviewRequest) -> dict[str, Any]:
    """Exactly what these skills will inject into a prompt, and how big it is."""
    return runner.preview(
        request.skill_ids, role=request.role, project_id=request.project_id
    )


class SuggestRequest(BaseModel):
    text: str
    project_id: str = ""
    limit: int = 5


@router.post("/suggest")
def suggest(request: SuggestRequest) -> dict[str, Any]:
    """Suggest skills whose triggers match some text (a project idea, a brief)."""
    if not request.text.strip():
        raise ValidationError("provide text to match against skill triggers")
    return {
        "suggestions": runner.suggest_skills(
            request.text, project_id=request.project_id, limit=request.limit
        )
    }

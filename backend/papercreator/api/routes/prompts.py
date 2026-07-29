"""CRUD for reusable, workbench-persisted prompt templates."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ...store import prompts as prompts_store

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


class PromptSave(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=200_000)
    description: str = Field(default="", max_length=2_000)
    project_id: str = ""


@router.get("")
def list_prompts(project_id: str = "") -> dict[str, Any]:
    return {"items": prompts_store.list_templates(project_id)}


@router.post("")
def create_prompt(body: PromptSave) -> dict[str, Any]:
    return {"template": prompts_store.save(**body.model_dump())}


@router.put("/{template_id}")
def update_prompt(template_id: str, body: PromptSave) -> dict[str, Any]:
    return {
        "template": prompts_store.save(template_id=template_id, **body.model_dump())
    }


@router.delete("/{template_id}")
def delete_prompt(template_id: str) -> dict[str, Any]:
    return {"deleted": prompts_store.delete(template_id), "id": template_id}

"""Reusable prompt templates stored inside the selected workbench.

Templates are deliberately separate from skills: a prompt is user-invoked text
that can be copied or inserted into chat, while a skill is standing instruction
automatically injected into selected agent runs.
"""

from __future__ import annotations

import re
from typing import Any

from ..core.db import dumps, execute, loads, query, query_one
from ..core.errors import NotFoundError, ValidationError
from ..core.util import new_id, utc_now_iso
from . import projects as projects_store

_VARIABLE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_.-]{0,63})\s*\}\}")


def _row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["variables"] = loads(data.get("variables"), []) or []
    data["scope"] = "project" if data.get("project_id") else "workbench"
    return data


def variables_in(content: str) -> list[str]:
    """Return unique ``{{variable}}`` names in first-seen order."""
    return list(dict.fromkeys(match.group(1) for match in _VARIABLE.finditer(content)))


def list_templates(project_id: str = "") -> list[dict[str, Any]]:
    if project_id:
        projects_store.require(project_id)
        rows = query(
            "SELECT * FROM prompt_templates WHERE project_id IS NULL OR project_id=? "
            "ORDER BY updated_at DESC, name COLLATE NOCASE",
            (project_id,),
        )
    else:
        rows = query(
            "SELECT * FROM prompt_templates WHERE project_id IS NULL "
            "ORDER BY updated_at DESC, name COLLATE NOCASE"
        )
    return [_row(row) for row in rows]


def get(template_id: str) -> dict[str, Any] | None:
    row = query_one("SELECT * FROM prompt_templates WHERE id=?", (template_id,))
    return _row(row) if row else None


def require(template_id: str) -> dict[str, Any]:
    template = get(template_id)
    if template is None:
        raise NotFoundError(f"prompt template {template_id} not found")
    return template


def save(
    *,
    name: str,
    content: str,
    description: str = "",
    project_id: str = "",
    template_id: str = "",
) -> dict[str, Any]:
    name = name.strip()
    content = content.strip()
    if not name:
        raise ValidationError("prompt template name is required")
    if not content:
        raise ValidationError("prompt template content is required")
    if len(name) > 160:
        raise ValidationError("prompt template name must be at most 160 characters")
    if len(content) > 200_000:
        raise ValidationError("prompt template content must be at most 200,000 characters")
    if project_id:
        projects_store.require(project_id)
    now = utc_now_iso()
    variables = variables_in(content)
    if template_id:
        require(template_id)
        execute(
            "UPDATE prompt_templates SET project_id=?, name=?, description=?, "
            "content=?, variables=?, updated_at=? WHERE id=?",
            (project_id or None, name, description.strip(), content, dumps(variables), now, template_id),
        )
        return require(template_id)
    template_id = new_id("prompt")
    execute(
        "INSERT INTO prompt_templates (id, project_id, name, description, content, "
        "variables, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (template_id, project_id or None, name, description.strip(), content, dumps(variables), now, now),
    )
    return require(template_id)


def delete(template_id: str) -> bool:
    require(template_id)
    return bool(execute("DELETE FROM prompt_templates WHERE id=?", (template_id,)).rowcount)

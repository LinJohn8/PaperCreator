"""Skill definition and SKILL.md parsing.

A *skill* is a folder containing a ``SKILL.md`` file: YAML-ish frontmatter for
metadata plus a Markdown body of instructions that gets injected into agent
prompts. That format was chosen because it is the one thing the user can author
three ways without friction - by hand in any editor, by asking an LLM to write
one, or by copying a folder from someone else - and because git diffs it usefully.

Layout::

    <skills-dir>/<skill-id>/
        SKILL.md          required: frontmatter + instructions
        examples/         optional: reference material, injected on request
        assets/           optional: templates the skill refers to

Frontmatter is parsed without a YAML dependency. The supported subset is
deliberately small (scalars, inline lists, block lists, quoted strings), because
a skill file the user hand-edits should fail loudly on unsupported syntax rather
than silently mis-parse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import ValidationError
from ..core.logging_setup import get_logger
from ..core.util import sha256_text, slugify

log = get_logger(__name__)

SKILL_FILENAME = "SKILL.md"

# Agent roles a skill can attach to. "all" means every role.
VALID_TARGETS = {
    "all", "planner", "reader", "synthesiser", "ideator", "outliner", "writer",
    "critic", "reviser", "citation_checker", "translator", "polisher",
}


@dataclass
class Skill:
    """One parsed skill."""

    id: str
    name: str
    description: str = ""
    version: str = "0.1.0"
    author: str = ""
    scope: str = "user"                  # builtin | user | project
    project_id: str = ""
    instructions: str = ""               # the Markdown body
    triggers: list[str] = field(default_factory=list)
    applies_to: list[str] = field(default_factory=lambda: ["all"])
    tags: list[str] = field(default_factory=list)
    priority: int = 50                   # lower renders first
    origin: str = "manual"               # manual | llm | builtin | imported
    path: str = ""
    checksum: str = ""
    enabled: bool = True
    # Optional structured extras a skill may declare.
    variables: dict[str, str] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)

    def applies_to_role(self, role: str) -> bool:
        if not self.applies_to or "all" in self.applies_to:
            return True
        return role in self.applies_to

    def to_record(self) -> dict[str, Any]:
        """Row shape for :mod:`store.skills_store`."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "scope": self.scope,
            "project_id": self.project_id or None,
            "description": self.description,
            "triggers": self.triggers,
            "applies_to": self.applies_to,
            "tags": self.tags,
            "path": self.path,
            "enabled": self.enabled,
            "author": self.author,
            "origin": self.origin,
            "checksum": self.checksum,
        }

    def render(self, *, include_examples: bool = False) -> str:
        """The prompt fragment this skill contributes."""
        parts = [f"### Skill: {self.name}"]
        if self.description:
            parts.append(f"_{self.description}_")
        parts.append(self.instructions.strip())
        if include_examples and self.examples:
            parts.append("#### Examples")
            parts.extend(self.examples)
        return "\n\n".join(p for p in parts if p.strip())


# ------------------------------------------------------------------- parsing

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_LIST_ITEM = re.compile(r"^\s*-\s+(.*)$")
_KEY_VALUE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")


def _strip_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``SKILL.md`` into (metadata, body).

    Supported frontmatter forms::

        name: My skill
        tags: [survey, chinese]
        applies_to:
          - writer
          - critic
        description: "quoted, with: a colon"

    Returns an empty dict when there is no frontmatter block, which is treated as
    a valid (if unnamed) skill so a plain instructions file still works.
    """
    match = _FRONTMATTER.match(text.lstrip("\ufeff"))
    if not match:
        return {}, text
    raw_meta, body = match.group(1), match.group(2)

    meta: dict[str, Any] = {}
    current_list_key: str | None = None
    for line in raw_meta.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        list_match = _LIST_ITEM.match(line)
        if list_match and current_list_key:
            meta.setdefault(current_list_key, []).append(
                _strip_quotes(list_match.group(1))
            )
            continue
        kv_match = _KEY_VALUE.match(line)
        if not kv_match:
            continue
        key, raw_value = kv_match.group(1).lower(), kv_match.group(2).strip()
        if not raw_value:
            # Start of a block list.
            current_list_key = key
            meta.setdefault(key, [])
            continue
        current_list_key = None
        if raw_value.startswith("[") and raw_value.endswith("]"):
            items = [
                _strip_quotes(part) for part in raw_value[1:-1].split(",")
                if part.strip()
            ]
            meta[key] = items
        elif raw_value.lower() in ("true", "false"):
            meta[key] = raw_value.lower() == "true"
        elif raw_value.isdigit():
            meta[key] = int(raw_value)
        else:
            meta[key] = _strip_quotes(raw_value)
    return meta, body


def parse_skill_text(
    text: str,
    *,
    skill_id: str = "",
    scope: str = "user",
    path: str = "",
    project_id: str = "",
) -> Skill:
    """Build a :class:`Skill` from ``SKILL.md`` content.

    Raises :class:`ValidationError` when the body is empty - a skill with
    metadata but no instructions would silently contribute nothing to prompts,
    which is worse than a clear error at import time.
    """
    meta, body = parse_frontmatter(text)
    instructions = body.strip()
    if not instructions:
        raise ValidationError(
            "SKILL.md has no instruction body. Put the guidance the agent should "
            "follow after the frontmatter block."
        )

    name = str(meta.get("name") or "").strip()
    resolved_id = str(meta.get("id") or skill_id or slugify(name, fallback="skill"))
    resolved_id = slugify(resolved_id, fallback="skill")

    applies_to = meta.get("applies_to") or meta.get("applies") or ["all"]
    if isinstance(applies_to, str):
        applies_to = [applies_to]
    normalised_targets: list[str] = []
    unknown_targets: list[str] = []
    for target in applies_to:
        key = str(target).strip().lower()
        if key in VALID_TARGETS:
            normalised_targets.append(key)
        elif key:
            unknown_targets.append(key)
    if unknown_targets:
        # Not fatal: a typo should not disable the skill, but it must be visible.
        log.warning(
            "skill '%s' lists unknown agent role(s) %s; valid roles are %s",
            resolved_id, unknown_targets, sorted(VALID_TARGETS),
        )
    if not normalised_targets:
        normalised_targets = ["all"]

    triggers = meta.get("triggers") or meta.get("when") or []
    if isinstance(triggers, str):
        triggers = [triggers]
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]

    variables: dict[str, str] = {}
    raw_variables = meta.get("variables")
    if isinstance(raw_variables, dict):
        variables = {str(k): str(v) for k, v in raw_variables.items()}

    return Skill(
        id=resolved_id,
        name=name or resolved_id.replace("-", " ").title(),
        description=str(meta.get("description") or "").strip(),
        version=str(meta.get("version") or "0.1.0"),
        author=str(meta.get("author") or "").strip(),
        scope=str(meta.get("scope") or scope),
        project_id=str(meta.get("project_id") or project_id or ""),
        instructions=instructions,
        triggers=[str(t).strip() for t in triggers if str(t).strip()],
        applies_to=normalised_targets,
        tags=[str(t).strip() for t in tags if str(t).strip()],
        priority=int(meta.get("priority") or 50),
        origin=str(meta.get("origin") or ("builtin" if scope == "builtin" else "manual")),
        path=path,
        checksum=sha256_text(text)[:32],
        enabled=bool(meta.get("enabled", True)),
        variables=variables,
    )


def load_skill_dir(
    directory: Path, *, scope: str = "user", project_id: str = ""
) -> Skill:
    """Read one skill folder. ``examples/*.md`` is attached if present."""
    skill_file = directory / SKILL_FILENAME
    if not skill_file.is_file():
        raise ValidationError(
            f"'{directory}' contains no {SKILL_FILENAME}",
            details={"path": str(directory)},
        )
    text = skill_file.read_text(encoding="utf-8-sig")
    skill = parse_skill_text(
        text, skill_id=directory.name, scope=scope, path=str(directory),
        project_id=project_id,
    )
    examples_dir = directory / "examples"
    if examples_dir.is_dir():
        for example in sorted(examples_dir.glob("*.md"))[:5]:
            try:
                skill.examples.append(example.read_text(encoding="utf-8-sig")[:4000])
            except OSError:
                continue
    return skill


def render_skill_file(skill: Skill) -> str:
    """Serialise a skill back to ``SKILL.md``.

    Used when a skill is authored through the UI or generated by an LLM: the file
    on disk stays the source of truth, so it must be written in the same format
    the parser reads.
    """
    lines = ["---", f"name: {skill.name}", f"id: {skill.id}"]
    if skill.description:
        lines.append(f"description: {_quote_if_needed(skill.description)}")
    lines.append(f"version: {skill.version}")
    if skill.author:
        lines.append(f"author: {_quote_if_needed(skill.author)}")
    lines.append(f"origin: {skill.origin}")
    lines.append(f"priority: {skill.priority}")
    if skill.applies_to:
        lines.append(f"applies_to: [{', '.join(skill.applies_to)}]")
    if skill.triggers:
        lines.append("triggers:")
        lines.extend(f"  - {_quote_if_needed(t)}" for t in skill.triggers)
    if skill.tags:
        lines.append(f"tags: [{', '.join(skill.tags)}]")
    lines.append("---")
    lines.append("")
    lines.append(skill.instructions.strip())
    lines.append("")
    return "\n".join(lines)


def _quote_if_needed(value: str) -> str:
    """Quote a scalar that would otherwise break the frontmatter parse."""
    if any(char in value for char in ":#[]{}\n") or value != value.strip():
        escaped = value.replace('"', '\\"').replace("\n", " ")
        return f'"{escaped}"'
    return value

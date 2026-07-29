"""Applying skills to agent prompts, and authoring skills with an LLM.

Two responsibilities:

**Rendering** (:func:`render_for_prompt`) - turn a set of selected skills into
one prompt fragment. Skills are appended to the *system* prompt so they read as
standing instructions and survive any trimming of the much larger user content.
Total size is capped: a user who enables twelve verbose skills would otherwise
crowd out the literature the agent needs to cite, which is a silent quality
regression rather than an error.

**Authoring** (:func:`draft_skill_with_llm`) - the requirement "skills can be
added through conversation with the LLM". The model produces the skill's
metadata and instructions as JSON; this module validates it and writes a real
``SKILL.md``, so an LLM-authored skill is indistinguishable from a hand-written
one afterwards.
"""

from __future__ import annotations

from typing import Any

from ..core.errors import ValidationError
from ..core.logging_setup import get_logger
from ..core.util import estimate_tokens, slugify
from ..store import skills_store
from . import loader
from .model import VALID_TARGETS, Skill

log = get_logger(__name__)

# Prompt budget for all skills combined. ~2000 tokens leaves room for the paper
# list, which is what actually determines citation quality.
MAX_SKILL_TOKENS = 2000


def render_for_prompt(
    skill_ids: list[str],
    *,
    role: str = "",
    project_id: str = "",
    include_examples: bool = False,
) -> tuple[str, list[str], list[str]]:
    """Render selected skills into a system-prompt fragment.

    Returns ``(text, used_ids, problems)``. ``role`` filters to skills that
    declare they apply to it, so a translation skill does not clutter the
    planner's prompt.
    """
    skills, problems = loader.load_many(skill_ids, project_id=project_id)
    if role:
        filtered = [s for s in skills if s.applies_to_role(role)]
        skills = filtered

    if not skills:
        return "", [], problems

    skills.sort(key=lambda s: (s.priority, s.name))
    parts: list[str] = []
    used: list[str] = []
    budget = MAX_SKILL_TOKENS
    for skill in skills:
        rendered = skill.render(include_examples=include_examples)
        cost = estimate_tokens(rendered)
        if cost > budget:
            problems.append(
                f"skill '{skill.id}' was skipped: the combined skill text would "
                f"exceed the {MAX_SKILL_TOKENS}-token prompt budget"
            )
            continue
        budget -= cost
        parts.append(rendered)
        used.append(skill.id)
        skills_store.record_use(skill.id)

    if not parts:
        return "", [], problems
    header = (
        "## Active skills\n"
        "The following project-specific instructions take precedence over your "
        "general guidance where they conflict."
    )
    return "\n\n".join([header, *parts]), used, problems


def suggest_skills(
    text: str, *, project_id: str = "", limit: int = 5
) -> list[dict[str, Any]]:
    """Suggest skills whose triggers match some text.

    Used to pre-select skills for a run from the project's idea or a section
    brief. Matching is substring on triggers and tags - simple and predictable,
    which matters because the user needs to understand why a skill was suggested.
    """
    lowered = (text or "").lower()
    if not lowered:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for skill in loader.discover(project_id=project_id):
        row = skills_store.get(skill.id) or {}
        if not row.get("enabled", True):
            continue
        hits: list[str] = []
        score = 0.0
        for trigger in skill.triggers:
            if trigger.lower() in lowered:
                score += 1.0
                hits.append(trigger)
        for tag in skill.tags:
            if tag.lower() in lowered:
                score += 0.4
                hits.append(tag)
        if score > 0:
            scored.append((score, {
                "id": skill.id, "name": skill.name,
                "description": skill.description,
                "matched": hits, "score": round(score, 2),
            }))
    scored.sort(key=lambda pair: -pair[0])
    return [item for _, item in scored[:limit]]


_AUTHOR_SYSTEM = """You write reusable "skills" for an academic paper writing tool.

A skill is a set of standing instructions injected into an AI writing agent's
system prompt. Good skills are specific, actionable, and about *how* to write -
not about a single paper's content.

Rules:
- instructions must be imperative and checkable ("Use British spelling", not
  "Consider spelling")
- 80-400 words of instructions; longer skills crowd out the literature the agent
  needs to see
- no invented facts about the user's field
- applies_to must use only these role names: all, planner, reader, synthesiser,
  ideator, outliner, writer, critic, reviser, citation_checker, translator,
  polisher
- triggers are short phrases that suggest this skill is relevant

Respond with STRICT JSON:
{"name": "...", "id": "kebab-case-id", "description": "one line",
 "applies_to": ["writer"], "triggers": ["..."], "tags": ["..."],
 "instructions": "The markdown instruction body.",
 "rationale": "why this skill is worded the way it is"}"""


async def draft_skill_with_llm(
    request: str,
    *,
    existing_skill_id: str = "",
    project_id: str = "",
    model: str = "",
) -> dict[str, Any]:
    """Ask the LLM to draft (or revise) a skill. Does not save it.

    Returning a draft rather than writing it directly is deliberate: a skill
    silently altering every future prompt is exactly the kind of change the user
    should approve first. The API exposes save as a separate call.
    """
    from ..llm import client as llm_client
    from ..llm import registry as llm_registry

    if not request.strip():
        raise ValidationError("describe the skill you want")
    if not llm_registry.has_any_provider():
        raise ValidationError(
            "no LLM provider is configured, so a skill cannot be drafted "
            "automatically. Write it by hand in Settings > Skills, or add an API "
            "key in Settings > Models."
        )

    context = f"USER REQUEST:\n{request.strip()}"
    if existing_skill_id:
        current = loader.get_skill(existing_skill_id, project_id=project_id)
        context += (
            f"\n\nREVISE THIS EXISTING SKILL (keep its id '{current.id}'):\n"
            f"name: {current.name}\ndescription: {current.description}\n"
            f"applies_to: {current.applies_to}\n"
            f"instructions:\n{current.instructions}"
        )

    payload = await llm_client.complete_json(
        context, system=_AUTHOR_SYSTEM, model=model,
        purpose="skill_authoring", max_tokens=2000,
    )
    if not isinstance(payload, dict):
        raise ValidationError("the model did not return a skill object")

    instructions = str(payload.get("instructions") or "").strip()
    if not instructions:
        raise ValidationError("the model returned a skill with no instructions")

    targets = payload.get("applies_to") or ["all"]
    if isinstance(targets, str):
        targets = [targets]
    valid_targets = [
        str(t).strip().lower() for t in targets
        if str(t).strip().lower() in VALID_TARGETS
    ] or ["all"]
    invalid = [
        str(t) for t in targets if str(t).strip().lower() not in VALID_TARGETS
    ]

    name = str(payload.get("name") or "").strip() or "Untitled skill"
    draft = {
        "id": slugify(
            str(payload.get("id") or existing_skill_id or name), fallback="skill"
        ),
        "name": name,
        "description": str(payload.get("description") or "").strip(),
        "applies_to": valid_targets,
        "triggers": [str(t).strip() for t in (payload.get("triggers") or [])
                     if str(t).strip()][:8],
        "tags": [str(t).strip() for t in (payload.get("tags") or [])
                 if str(t).strip()][:8],
        "instructions": instructions,
        "rationale": str(payload.get("rationale") or ""),
        "origin": "llm",
        "estimated_prompt_tokens": estimate_tokens(instructions),
        "warnings": (
            [f"ignored unknown role name(s): {', '.join(invalid)}"] if invalid else []
        ),
    }
    if draft["estimated_prompt_tokens"] > 700:
        draft["warnings"].append(
            f"these instructions are large (~{draft['estimated_prompt_tokens']} "
            f"tokens) and will consume a noticeable share of the prompt budget"
        )
    return draft


def save_draft(draft: dict[str, Any], *, scope: str = "user",
               project_id: str = "", overwrite: bool = False) -> Skill:
    """Persist a drafted skill (from the LLM or the UI form)."""
    return loader.save_skill(
        name=str(draft.get("name") or ""),
        instructions=str(draft.get("instructions") or ""),
        skill_id=str(draft.get("id") or ""),
        description=str(draft.get("description") or ""),
        applies_to=list(draft.get("applies_to") or ["all"]),
        triggers=list(draft.get("triggers") or []),
        tags=list(draft.get("tags") or []),
        author=str(draft.get("author") or ""),
        origin=str(draft.get("origin") or "manual"),
        scope=scope,
        project_id=project_id,
        priority=int(draft.get("priority") or 50),
        overwrite=overwrite,
    )


def preview(skill_ids: list[str], *, role: str = "", project_id: str = "") -> dict[str, Any]:
    """Show exactly what will be injected, for the "preview prompt" button."""
    text, used, problems = render_for_prompt(
        skill_ids, role=role, project_id=project_id
    )
    return {
        "text": text,
        "used": used,
        "problems": problems,
        "estimated_tokens": estimate_tokens(text),
        "budget_tokens": MAX_SKILL_TOKENS,
    }

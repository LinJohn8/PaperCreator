"""Agent routes: start runs, inspect steps, preview prompts.

Runs are always background jobs - a full-auto pipeline is dozens of model calls
over several minutes. Progress, per-step results and streaming token deltas all
arrive on ``/api/system/events``.

``POST /api/agents/preview`` exists because an agent that writes a bad section is
usually a prompt problem, and the user needs to be able to see the exact prompt
(including which papers made it into context and which skills were active) without
spending a model call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ...core.errors import ConflictError, NotFoundError, ValidationError
from ...core.logging_setup import get_logger
from ...core.util import new_id
from ...store import projects as projects_store
from ...store import runs as runs_store

log = get_logger(__name__)
router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("/pipelines")
def list_pipelines() -> dict[str, Any]:
    from ...agents import orchestrator, roles

    return {
        "pipelines": orchestrator.describe_pipelines(),
        "roles": roles.describe_roles(),
    }


class RunRequest(BaseModel):
    project_id: str
    pipeline: str = "full_auto"
    analysis_id: str = ""
    paper_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    custom_roles: list[str] = Field(default_factory=list)
    # Config overrides; None means "use the setting".
    model: str | None = None
    fast_model: str | None = None
    temperature: float | None = None
    language: str | None = None
    bilingual: bool | None = None
    target_words: int | None = None
    max_papers_in_context: int | None = None
    max_notes: int | None = None
    enable_critique: bool | None = None
    enable_translation: bool | None = None
    section_keys: list[str] = Field(default_factory=list)
    token_budget: int | None = None

    def overrides(self) -> dict[str, Any]:
        data = self.model_dump(
            exclude={"project_id", "pipeline", "analysis_id", "paper_ids",
                     "skill_ids", "custom_roles"}
        )
        # section_keys is a list, so an empty one is meaningful ("all sections")
        # and must not be treated as "unset".
        return {
            k: v for k, v in data.items()
            if v is not None and (k != "section_keys" or v)
        }


@router.post("/run")
def start_run(request: RunRequest) -> dict[str, Any]:
    """Queue an agent pipeline. Returns ``{job_id, run_id}``."""
    from ...agents import orchestrator
    from ...llm import registry as llm_registry

    projects_store.require(request.project_id)
    if request.pipeline not in orchestrator.PIPELINES:
        raise ValidationError(
            f"unknown pipeline '{request.pipeline}'. Available: "
            f"{', '.join(orchestrator.PIPELINES)}"
        )
    if request.pipeline == "custom" and not request.custom_roles:
        raise ValidationError(
            "the custom pipeline needs custom_roles - an ordered list of agent "
            "role names"
        )
    if request.pipeline == "section" and not request.section_keys:
        log.info(
            "section pipeline started with no section_keys; every outlined section "
            "will be drafted"
        )
    if not llm_registry.has_any_provider():
        raise ValidationError(
            "no LLM provider is configured, so agents cannot run. Add an API key "
            "in Settings > Models, or run a local model with Ollama."
        )

    result = orchestrator.submit_run(
        project_id=request.project_id,
        pipeline=request.pipeline,
        config_overrides=request.overrides(),
        analysis_id=request.analysis_id,
        paper_ids=request.paper_ids or None,
        skill_ids=request.skill_ids or None,
        custom_roles=request.custom_roles or None,
    )
    log.info(
        "queued %s run %s for project %s",
        request.pipeline, result["run_id"], request.project_id,
    )
    return {
        **result,
        "pipeline": request.pipeline,
        "note": "subscribe to /api/system/events for step progress and streaming "
                "text",
    }


@router.get("/runs")
def list_runs(
    project_id: str = "", status: str = "", limit: int = Query(50, le=200)
) -> dict[str, Any]:
    return {
        "items": runs_store.list_runs(project_id, status=status, limit=limit)
    }


@router.get("/evaluations/summary")
def evaluation_summary(
    project_id: str = "", limit: int = Query(500, ge=1, le=2000)
) -> dict[str, Any]:
    """Latest-decision quality aggregates plus multi-review disagreement signals."""

    if project_id:
        projects_store.require(project_id)
    return runs_store.evaluation_summary(project_id, limit=limit)


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    include_prompts: bool = Query(
        False, description="include full prompt text (large)"
    ),
) -> dict[str, Any]:
    """A run with its steps. Prompts are omitted unless asked for.

    Full prompts are kept so a bad output can be audited, but they are large -
    a full-auto run's prompts total hundreds of kilobytes.
    """
    run = runs_store.get_run(run_id, with_steps=False)
    if run is None:
        raise NotFoundError(f"agent run {run_id} not found")
    run["steps"] = runs_store.list_steps(run_id, include_prompt=include_prompts)
    return run


def _build_review_packet(run_id: str, kind: str) -> dict[str, Any]:
    from ...agents import review as review_evidence

    run = runs_store.require_run(run_id)
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    report = result.get("quality_report")
    report = report if isinstance(report, dict) else {}
    registry = report.get("citation_registry")
    registry = registry if isinstance(registry, dict) else {}
    sources = registry.get("papers")
    sources = sources if isinstance(sources, list) else []
    steps = runs_store.list_steps(run_id, include_prompt=False)
    return review_evidence.build_packet(
        run,
        source_evidence=[item for item in sources if isinstance(item, dict)],
        steps=steps,
        kind=kind,
    )


@router.get("/runs/{run_id}/review-packet")
def get_review_packet(
    run_id: str,
    kind: Literal["blind", "analysis"] = Query("blind"),
) -> dict[str, Any]:
    """Return a blind scoring packet or an identity-bearing analysis packet."""

    return _build_review_packet(run_id, kind)


class ReviewPacketExportRequest(BaseModel):
    kind: Literal["blind", "analysis"] = "blind"


@router.post("/runs/{run_id}/review-packet/export")
def export_review_packet(
    run_id: str, request: ReviewPacketExportRequest
) -> dict[str, Any]:
    """Persist a packet below the selected workbench's project export tree."""

    run = runs_store.require_run(run_id)
    project = projects_store.require(str(run.get("project_id") or ""))
    packet = _build_review_packet(run_id, request.kind)
    directory = projects_store.project_root(project) / "exports" / "reviews"
    directory.mkdir(parents=True, exist_ok=True)
    filename = (
        f"{packet['sample_id']}-{packet['packet_fingerprint'][:12]}-"
        f"{request.kind}.json"
    )
    target = (directory / filename).resolve()
    root = directory.resolve()
    try:
        inside = target.is_relative_to(root)
    except (OSError, ValueError):
        inside = False
    if not inside:
        raise ValidationError("review export path escaped the project directory")
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConflictError(
                "an existing content-addressed review packet is unreadable; "
                "it was not overwritten",
                code="review_packet_export_conflict",
                details={"path": str(target), "reason": str(exc)},
            ) from exc
        if existing.get("packet_fingerprint") != packet["packet_fingerprint"]:
            raise ConflictError(
                "an existing content-addressed review packet has different content; "
                "it was not overwritten",
                code="review_packet_export_conflict",
                details={"path": str(target)},
            )
    else:
        temporary = directory / f".{filename}.{new_id('tmp')}.tmp"
        try:
            temporary.write_text(
                json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return {
        "path": str(target),
        "packet_kind": request.kind,
        "sample_id": packet["sample_id"],
        "packet_fingerprint": packet["packet_fingerprint"],
        "bytes": target.stat().st_size,
    }


@router.get("/runs/{run_id}/steps/{step_id}")
def get_step(step_id: str, run_id: str) -> dict[str, Any]:
    """One step including its full prompt - the audit view."""
    step = runs_store.get_step(step_id)
    if step is None or step.get("run_id") != run_id:
        raise NotFoundError(f"step {step_id} not found in run {run_id}")
    return step


class HumanEvaluationRequest(BaseModel):
    """Versioned human rubric for research quality that automation cannot prove."""

    reviewer: str = Field(default="", max_length=120)
    decision: Literal["accepted", "revision_required", "rejected"] = (
        "revision_required"
    )
    factual_grounding: int = Field(ge=1, le=5)
    citation_support: int = Field(ge=1, le=5)
    methodological_soundness: int = Field(ge=1, le=5)
    literature_coverage: int = Field(ge=1, le=5)
    argument_coherence: int = Field(ge=1, le=5)
    writing_clarity: int = Field(ge=1, le=5)
    source_evidence_checked: bool = False
    automatic_warnings_acknowledged: bool = False
    reviewed_section_keys: list[str] = Field(default_factory=list, max_length=100)
    reviewed_paper_ids: list[str] = Field(default_factory=list, max_length=1000)
    reviewed_manuscript_fingerprint: str = Field(default="", max_length=64)
    review_mode: Literal["identified", "blind"] = "identified"
    notes: str = Field(default="", max_length=8000)


@router.post("/runs/{run_id}/evaluations")
def add_human_evaluation(
    run_id: str, request: HumanEvaluationRequest
) -> dict[str, Any]:
    """Append, never overwrite, a human acceptance/revision/rejection record.

    Rubric v3 only accepts a completed run whose deterministic gate is pass/warn,
    whose immutable manuscript and source evidence were explicitly reviewed,
    and whose reviewer left an evidence note. Older v1/v2 records remain
    immutable and visible, but an unbound legacy run cannot gain a new v3
    acceptance label.
    """

    run = runs_store.require_run(run_id)
    if run["status"] not in {"done", "failed", "cancelled"}:
        raise ConflictError(
            "a running Agent result cannot be quality-reviewed yet",
            code="agent_run_not_terminal",
            details={"run_id": run_id, "status": run["status"]},
        )
    dimensions = {
        "factual_grounding": request.factual_grounding,
        "citation_support": request.citation_support,
        "methodological_soundness": request.methodological_soundness,
        "literature_coverage": request.literature_coverage,
        "argument_coherence": request.argument_coherence,
        "writing_clarity": request.writing_clarity,
    }
    reviewed_keys = list(
        dict.fromkeys(
            key.strip() for key in request.reviewed_section_keys if key.strip()
        )
    )
    reviewed_paper_ids = list(
        dict.fromkeys(
            paper_id.strip()
            for paper_id in request.reviewed_paper_ids
            if paper_id.strip()
        )
    )
    from ...agents import quality as quality_evidence

    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    quality_report = result.get("quality_report")
    quality_report = quality_report if isinstance(quality_report, dict) else {}
    manuscript_snapshot = result.get("review_manuscript")
    manuscript_snapshot = (
        manuscript_snapshot if isinstance(manuscript_snapshot, dict) else {}
    )
    review_target = quality_evidence.build_review_target(
        quality_report,
        run_status=run["status"],
        manuscript_snapshot=manuscript_snapshot,
    )
    submitted_fingerprint = request.reviewed_manuscript_fingerprint
    expected_fingerprint = str(review_target.get("manuscript_fingerprint") or "")
    if submitted_fingerprint and submitted_fingerprint != expected_fingerprint:
        raise ConflictError(
            "the manuscript evidence changed after this review form was opened",
            code="agent_evaluation_stale_manuscript",
            details={
                "submitted_manuscript_fingerprint": submitted_fingerprint,
                "expected_manuscript_fingerprint": expected_fingerprint,
            },
        )
    report_sections = quality_report.get("sections")
    report_sections = report_sections if isinstance(report_sections, list) else []
    known_section_keys = {
        str(section.get("section_key") or "")
        for section in report_sections
        if isinstance(section, dict) and str(section.get("section_key") or "")
    }
    registry = quality_report.get("citation_registry")
    registry = registry if isinstance(registry, dict) else {}
    known_paper_ids = {
        str(paper_id)
        for paper_id in registry.get("cited_paper_ids") or []
        if str(paper_id)
    }
    unknown_sections = sorted(set(reviewed_keys).difference(known_section_keys))
    unknown_papers = sorted(set(reviewed_paper_ids).difference(known_paper_ids))
    if unknown_sections or unknown_papers:
        raise ValidationError(
            "the review names sections or papers outside this quality report",
            code="agent_evaluation_unknown_review_targets",
            details={
                "unknown_section_keys": unknown_sections,
                "unknown_paper_ids": unknown_papers,
            },
        )

    if request.decision == "accepted":
        required_sections = set(review_target["required_section_keys"])
        required_papers = set(review_target["required_paper_ids"])
        missing_sections = sorted(required_sections.difference(reviewed_keys))
        missing_papers = sorted(required_papers.difference(reviewed_paper_ids))
        blockers: list[str] = []
        if run["status"] != "done":
            blockers.append("run_not_done")
        if not quality_report:
            blockers.append("quality_report_missing")
        if review_target["rubric_version"] < 3:
            blockers.append("immutable_manuscript_missing")
        if review_target.get("manuscript_integrity") != "pass":
            blockers.append("immutable_manuscript_integrity_failed")
        if (
            not request.reviewed_manuscript_fingerprint
            or request.reviewed_manuscript_fingerprint
            != review_target.get("manuscript_fingerprint")
        ):
            blockers.append("reviewed_manuscript_fingerprint_mismatch")
        if review_target["automatic_gate"] not in {"pass", "warn"}:
            blockers.append("automatic_gate_not_acceptable")
        if not required_sections:
            blockers.append("no_modified_sections")
        if missing_sections:
            blockers.append("sections_not_reviewed")
        if missing_papers:
            blockers.append("cited_papers_not_reviewed")
        if not request.source_evidence_checked:
            blockers.append("source_evidence_not_confirmed")
        if min(dimensions.values()) < 3:
            blockers.append("rubric_score_below_minimum")
        if not request.reviewer.strip():
            blockers.append("reviewer_missing")
        if len(request.notes.strip()) < 20:
            blockers.append("evidence_notes_too_short")
        if (
            review_target["automatic_gate"] == "warn"
            and not request.automatic_warnings_acknowledged
        ):
            blockers.append("automatic_warnings_not_acknowledged")
        if blockers:
            raise ValidationError(
                "acceptance requires a completed structurally eligible run, full "
                "section/source coverage, an intact frozen manuscript, warning "
                "acknowledgement, an identified reviewer, evidence notes, and "
                "every rubric score at least 3",
                code="agent_evaluation_acceptance_requirements",
                details={
                    "blockers": blockers,
                    "run_status": run["status"],
                    "automatic_gate": review_target["automatic_gate"],
                    "source_evidence_checked": request.source_evidence_checked,
                    "automatic_warnings_acknowledged": (
                        request.automatic_warnings_acknowledged
                    ),
                    "minimum_score": min(dimensions.values()),
                    "missing_section_keys": missing_sections,
                    "missing_paper_ids": missing_papers,
                    "reviewer_present": bool(request.reviewer.strip()),
                    "evidence_notes_length": len(request.notes.strip()),
                    "manuscript_integrity": review_target.get(
                        "manuscript_integrity"
                    ),
                    "manuscript_integrity_problems": review_target.get(
                        "manuscript_integrity_problems", []
                    ),
                    "expected_manuscript_fingerprint": review_target.get(
                        "manuscript_fingerprint", ""
                    ),
                },
            )
    evaluation = runs_store.append_human_evaluation(
        run_id,
        {
            "rubric_version": review_target["rubric_version"],
            "reviewer": request.reviewer.strip(),
            "decision": request.decision,
            "dimensions": dimensions,
            "source_evidence_checked": request.source_evidence_checked,
            "automatic_warnings_acknowledged": (
                request.automatic_warnings_acknowledged
            ),
            "reviewed_section_keys": reviewed_keys,
            "reviewed_paper_ids": reviewed_paper_ids,
            "reviewed_manuscript_fingerprint": (
                request.reviewed_manuscript_fingerprint
            ),
            "review_mode": request.review_mode,
            "notes": request.notes.strip(),
            "review_target": review_target,
        },
    )
    updated = runs_store.require_run(run_id)
    updated["evaluation"] = evaluation
    return updated


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict[str, Any]:
    """Cancel a running pipeline.

    Cooperative: the run stops at its next step boundary or streaming chunk, and
    everything already written to the manuscript is kept.
    """
    from ...core.jobs import manager

    run = runs_store.get_run(run_id, with_steps=False)
    if run is None:
        raise NotFoundError(f"agent run {run_id} not found")

    cancelled = False
    for job in manager.list(limit=100):
        if (job.get("payload") or {}).get("run_id") == run_id:
            cancelled = manager.cancel(job["id"])
            break
    return {
        "run_id": run_id,
        "requested": cancelled,
        "note": "the run stops at its next checkpoint; work already written to the "
                "manuscript is kept, and a post-run snapshot is still taken",
    }


@router.delete("/runs/{run_id}")
def delete_run(run_id: str) -> dict[str, Any]:
    return {"deleted": runs_store.delete_run(run_id)}


class PreviewRequest(BaseModel):
    project_id: str
    role: str = "writer"
    section_key: str = ""
    skill_ids: list[str] = Field(default_factory=list)
    analysis_id: str = ""
    max_papers_in_context: int = 40


@router.post("/preview")
def preview_prompt(request: PreviewRequest) -> dict[str, Any]:
    """Show what an agent would be told, without calling a model.

    Reveals the assembled context: which papers are included, which skills are
    active, the system prompt, and the estimated size. This is the debugging tool
    for "why did the agent write that?".
    """
    from ...agents import orchestrator, prompts
    from ...agents.roles import ALL_ROLES
    from ...core.util import estimate_tokens
    from ...skills import runner as skills_runner

    if request.role not in ALL_ROLES:
        raise ValidationError(
            f"unknown role '{request.role}'. Available: "
            f"{', '.join(sorted(ALL_ROLES))}"
        )
    project = projects_store.require(request.project_id)
    board = orchestrator.load_blackboard(
        request.project_id, analysis_id=request.analysis_id
    )
    papers = board.top_papers(request.max_papers_in_context)
    keys = prompts.build_citation_keys(board.papers or papers)

    system_prompt = {
        "planner": prompts.PLANNER, "reader": prompts.READER,
        "synthesiser": prompts.SYNTHESISER, "ideator": prompts.IDEATOR,
        "outliner": prompts.OUTLINER, "writer": prompts.WRITER,
        "critic": prompts.CRITIC, "reviser": prompts.REVISER,
        "citation_checker": prompts.CITATION_AGENT,
        "translator": prompts.TRANSLATOR, "polisher": prompts.POLISHER,
    }.get(request.role, "")

    skills_text, skills_used, skill_problems = skills_runner.render_for_prompt(
        request.skill_ids, role=request.role, project_id=request.project_id
    )

    context_parts = [
        prompts.format_project_context(project),
        prompts.format_analysis_context(board.analysis),
        f"LITERATURE ({len(papers)} of {len(board.papers)} papers):\n"
        + prompts.format_paper_list(papers, keys, notes=board.paper_notes),
    ]
    if request.section_key:
        section = next(
            (s for s in board.outline if s["key"] == request.section_key), None
        )
        if section is None:
            raise NotFoundError(
                f"section '{request.section_key}' is not in this project's outline"
            )
        context_parts.insert(
            1,
            f"SECTION: {section['title']}\nBRIEF: {section['guidance']}\n"
            f"TARGET: {section['target_words']} words\n"
            f"ASSIGNED PAPERS: {len(section['paper_ids'])}",
        )

    user_prompt = "\n\n".join(p for p in context_parts if p)
    full_system = f"{system_prompt}\n\n{skills_text}".strip() if skills_text else system_prompt
    return {
        "role": request.role,
        "section_key": request.section_key,
        "system_prompt": full_system,
        "user_prompt": user_prompt,
        "skills_used": skills_used,
        "skill_problems": skill_problems,
        "papers_in_context": [
            {"key": keys.get(p.id, ""), "title": p.title, "year": p.year,
             "has_note": p.id in board.paper_notes,
             "abstract_chars": len(p.abstract)}
            for p in papers
        ],
        "estimated_tokens": {
            "system": estimate_tokens(full_system),
            "user": estimate_tokens(user_prompt),
            "total": estimate_tokens(full_system) + estimate_tokens(user_prompt),
        },
        "blackboard": board.snapshot(),
    }


@router.get("/blackboard/{project_id}")
def blackboard_state(project_id: str, analysis_id: str = "") -> dict[str, Any]:
    """What the agents would start from, rebuilt from the database.

    Useful before a section-mode run: it shows whether notes, themes and an
    outline already exist, which determines how much the run has to redo.
    """
    from ...agents import orchestrator

    board = orchestrator.load_blackboard(project_id, analysis_id=analysis_id)
    return {
        "project_id": project_id,
        "papers": len(board.papers),
        "has_analysis": bool(board.analysis),
        "analysis_id": board.analysis.get("id", ""),
        "notes": len(board.paper_notes),
        "themes": len(board.themes),
        "outline": [
            {"key": s["key"], "title": s["title"],
             "target_words": s["target_words"], "papers": len(s["paper_ids"])}
            for s in board.outline
        ],
        "drafted_sections": sorted(board.sections),
        "translated_sections": sorted(board.translations),
    }

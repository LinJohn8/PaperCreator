"""Agent orchestration: the pipelines that compose roles into work.

The user asked for two modes explicitly - "let the AI do the whole thing" and
"do each part separately, then join them up". Both are here, plus the pieces
needed to make the second one honest:

``full_auto``
    plan -> read -> synthesise -> validate gaps -> outline -> draft every
    section -> review + revise -> check citations -> harmonise -> (translate).
    One click, one run, everything written to the manuscript.

``section``
    Draft (or redraft) specific sections only, reusing whatever the blackboard
    can be rebuilt from - existing notes, an existing outline, already-drafted
    neighbours. This is what makes "redo the methods section" cheap.

``stitch``
    Take sections that were drafted independently (by the agent, by the user, or
    both) and make them one paper: harmonise terminology, fix transitions,
    verify citations across the whole text.

``custom``
    An explicit list of role names, for skills and power users.

Guarantees that hold for every pipeline:

* **Nothing is lost.** A ``pre_agent`` snapshot is taken before the first write
  and a ``post_agent`` snapshot after the last, so any run is fully revertable
  from the Versions view.
* **Budget is enforced.** Token spend is checked between steps against
  ``RunConfig.token_budget``; exceeding it stops the run cleanly with everything
  written so far kept.
* **Cancellation works.** Every step checks the job's cancel flag, and streaming
  steps check between deltas.
* **Partial success is preserved.** A failure in one section's writer does not
  discard the sections already drafted.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..core import events
from ..core.config import get_settings
from ..core.errors import ConfigurationError, ValidationError, error_diagnostics
from ..core.jobs import JobCancelled, JobContext, manager
from ..core.logging_setup import get_logger
from ..core.models import ProjectModel
from ..llm import registry as llm_registry
from ..store import analyses as analyses_store
from ..store import documents as documents_store
from ..store import papers as papers_store
from ..store import projects as projects_store
from ..store import runs as runs_store
from ..store import snapshots as snapshots_store
from ..writing import citations as citations_module
from . import quality
from .base import Blackboard, RunConfig
from .roles import (
    ALL_ROLES,
    CitationAgent,
    CriticAgent,
    IdeatorAgent,
    OutlinerAgent,
    PlannerAgent,
    PolisherAgent,
    ReaderAgent,
    ReviserAgent,
    SynthesiserAgent,
    TranslatorAgent,
    WriterAgent,
)

log = get_logger(__name__)

PIPELINES = ("full_auto", "section", "stitch", "custom")


class BudgetExceeded(Exception):
    """Raised between steps when the run's token budget is spent."""


def build_run_config(
    project: ProjectModel, overrides: dict[str, Any] | None = None
) -> RunConfig:
    """Config from settings + project + request overrides (last wins)."""
    settings = get_settings()
    config = RunConfig(
        model=settings.llm.default_chat,
        fast_model=settings.llm.default_fast,
        temperature=settings.llm.temperature,
        max_tokens=settings.llm.max_output_tokens,
        language=project.language or settings.writing.default_language,
        bilingual=project.bilingual and settings.writing.bilingual,
        citation_style=project.citation_style or settings.writing.citation_style,
        token_budget=settings.llm.run_token_budget,
    )
    if overrides:
        for key, value in overrides.items():
            if value is None or not hasattr(config, key):
                continue
            setattr(config, key, value)
    config.enable_translation = config.enable_translation or config.bilingual
    return config


def load_blackboard(
    project_id: str,
    *,
    analysis_id: str = "",
    paper_ids: list[str] | None = None,
    include_existing_sections: bool = True,
) -> Blackboard:
    """Assemble the run's starting state from the database.

    Existing section text is loaded by default so that a section-mode run knows
    what its neighbours already say (and can avoid repeating them), and so
    ``stitch`` has something to work with.
    """
    project = projects_store.require(project_id)
    project_paper_ids = papers_store.project_paper_ids(project_id)
    ids = paper_ids or project_paper_ids
    papers = papers_store.get_many(ids)

    # Citation keys are a project-level registry, not a per-request numbering.
    # Otherwise selecting only one of two same-author/year papers changes its key
    # and breaks citations written by a previous section run or by export.
    citation_ids = list(dict.fromkeys([*project_paper_ids, *ids]))
    citation_papers = papers_store.get_many(citation_ids)

    analysis: dict[str, Any] = {}
    resolved_analysis_id = analysis_id or analyses_store.latest_analysis_id(project_id)
    if resolved_analysis_id:
        stored = analyses_store.get_analysis(resolved_analysis_id, with_points=False)
        if stored is not None:
            analysis = {
                "id": stored.id,
                "clusters": [c.model_dump() for c in stored.clusters],
                "gaps": [g.model_dump() for g in stored.gaps],
                "keywords": [k.model_dump() for k in stored.keywords[:40]],
                "metrics": stored.metrics,
                "n_papers": stored.n_papers,
                "n_clusters": stored.n_clusters,
            }

    board = Blackboard(project=project, papers=papers, analysis=analysis)
    board.extra["citation_keys"] = dict(
        citations_module.CitationKeyMap.build(citation_papers).by_paper
    )
    board.extra["citation_paper_ids"] = [paper.id for paper in citation_papers]
    if include_existing_sections:
        document = documents_store.primary_document(project_id)
        outline: list[dict[str, Any]] = []
        for section in documents_store.list_sections(document.id):
            if section.content.strip():
                board.sections[section.key] = section.content
            if section.content_zh.strip():
                board.translations[section.key] = section.content_zh
            board.citations[section.key] = list(section.cited_paper_ids)
            outline.append({
                "key": section.key,
                "title": section.title,
                "level": section.level,
                "guidance": section.guidance,
                "opening": "",
                "must_not": "",
                "target_words": section.target_words or 600,
                "paper_ids": list(section.cited_paper_ids),
                "ordering": section.ordering,
            })
        if outline:
            board.outline = outline
        board.extra["document_id"] = document.id
    return board


class Orchestrator:
    """Runs one pipeline over one project."""

    def __init__(
        self,
        *,
        project_id: str,
        pipeline: str,
        config: RunConfig,
        run_id: str,
        job: JobContext | None = None,
        analysis_id: str = "",
        paper_ids: list[str] | None = None,
        skill_ids: list[str] | None = None,
        custom_roles: list[str] | None = None,
    ) -> None:
        if pipeline not in PIPELINES:
            raise ValidationError(
                f"unknown pipeline '{pipeline}'. Available: {', '.join(PIPELINES)}"
            )
        self.project_id = project_id
        self.pipeline = pipeline
        self.config = config
        self.run_id = run_id
        self.job = job
        self.analysis_id = analysis_id
        self.paper_ids = paper_ids
        self.skill_ids = skill_ids or []
        self.custom_roles = custom_roles or []
        self.skills_text = ""
        self.step_index = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.warnings: list[str] = []
        self.failures: list[dict[str, Any]] = []
        self._failure_exceptions: list[Exception] = []
        self.board: Blackboard | None = None

    # ----------------------------------------------------------- lifecycle
    def _check_budget(self) -> None:
        # The usage ledger includes failed calls, JSON repair retries and
        # interrupted streams; the in-memory step totals do not. Budget against
        # the durable total so provider failures cannot bypass the ceiling.
        audit = runs_store.get_run(self.run_id, with_steps=False)
        spent = (
            int(audit.get("tokens_in") or 0) + int(audit.get("tokens_out") or 0)
            if audit else self.tokens_in + self.tokens_out
        )
        if self.config.token_budget and spent >= self.config.token_budget:
            raise BudgetExceeded(
                f"token budget exhausted ({spent:,} of "
                f"{self.config.token_budget:,}); stopping with work completed so far"
            )

    def _make(self, role_class: type, **kwargs: Any) -> Any:
        return role_class(
            config=self.config, run_id=self.run_id, job=self.job,
            skills_text=self.skills_text, **kwargs,
        )

    async def _step(self, agent: Any, board: Blackboard) -> Any:
        self._check_budget()
        self.step_index += 1
        if self.job is not None:
            self.job.log(f"{agent.title}")
        result = await agent.execute(board, ordering=self.step_index * 10)
        self.tokens_in += result.tokens_in
        self.tokens_out += result.tokens_out
        self.warnings.extend(
            f"{agent.name}: {w}" for w in (result.warnings or [])
        )
        return result

    def _remember_failure(self, exc: Exception, *, context: str) -> None:
        """Keep a structured failure while allowing independent work to continue."""
        failure = error_diagnostics(exc)
        failure["context"] = context
        # Full partial text is already stored on the failed step.  The run-level
        # summary only needs its size and recovery metadata.
        failure.pop("partial_output", None)
        self.failures.append(failure)
        self._failure_exceptions.append(exc)

    async def run(self) -> dict[str, Any]:
        if not llm_registry.has_any_provider():
            raise ConfigurationError(
                "no LLM provider is configured, so agents cannot run. Add an API "
                "key in Settings > Models, or run a local model with Ollama."
            )
        started = time.perf_counter()
        runs_store.start_run(self.run_id)
        events.publish(
            events.AGENT_RUN_STARTED,
            {"runId": self.run_id, "pipeline": self.pipeline},
            project_id=self.project_id,
            job_id=self.job.job_id if self.job else None,
        )

        # Load skills before anything else: they modify every prompt.
        self.skills_text = self._load_skills()

        board = load_blackboard(
            self.project_id, analysis_id=self.analysis_id, paper_ids=self.paper_ids
        )
        self.board = board
        if not board.papers and self.pipeline in ("full_auto", "section"):
            self.warnings.append(
                "this project has no papers linked; the agents will have no "
                "literature to cite. Run a search first."
            )

        pre_snapshot = snapshots_store.capture(
            self.project_id,
            label=f"before {self.pipeline} run",
            kind="pre_agent",
        )

        outcome: dict[str, Any]
        status = "done"
        error = ""
        failure_exc: Exception | None = None
        try:
            if self.pipeline == "full_auto":
                outcome = await self._run_full_auto(board)
            elif self.pipeline == "section":
                outcome = await self._run_sections(board)
            elif self.pipeline == "stitch":
                outcome = await self._run_stitch(board)
            else:
                outcome = await self._run_custom(board)
        except JobCancelled as exc:
            status = "cancelled"
            error = "cancelled by user"
            outcome = {"cancelled": True, "reason": error}
            failure_exc = exc
            log.info("agent run %s cancelled; preserving completed work", self.run_id)
        except BudgetExceeded as exc:
            status = "done"
            error = str(exc)
            self.warnings.append(str(exc))
            outcome = {"stopped_early": True, "reason": str(exc)}
            log.warning("run %s stopped: %s", self.run_id, exc)
        except Exception as exc:  # noqa: BLE001 - persist partial work, then report
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
            outcome = {"failed": True, "reason": error}
            self._remember_failure(exc, context="pipeline")
            failure_exc = exc
            log.exception("agent run %s failed", self.run_id)

        if status == "done" and self.failures:
            status = "failed"
            first = self.failures[0]
            error = f"{first.get('error_type', 'Error')}: {first.get('message', '')}"
            outcome = {**outcome, "failed": True, "reason": error}
            failure_exc = self._failure_exceptions[0]

        # Persist whatever was produced, even on failure - a run that drafted
        # four of six sections must not throw those four away.
        written = {"sections": 0, "document_id": str(board.extra.get("document_id") or "")}
        try:
            written = self._persist(board)
        except Exception as exc:  # persistence failures must not leave a running row
            self._remember_failure(exc, context="persisting partial manuscript")
            if failure_exc is None:
                failure_exc = exc
                error = f"{type(exc).__name__}: {exc}"
            status = "failed"
            outcome = {**outcome, "failed": True, "reason": error}
            log.exception("agent run %s could not persist its blackboard", self.run_id)

        post_snapshot: dict[str, Any] | None = None
        try:
            post_snapshot = snapshots_store.capture(
                self.project_id, label=f"after {self.pipeline} run", kind="post_agent"
            )
        except Exception as exc:  # the pre-run recovery point still exists
            self._remember_failure(exc, context="capturing post-run snapshot")
            if failure_exc is None:
                failure_exc = exc
                error = f"{type(exc).__name__}: {exc}"
            status = "failed"
            outcome = {**outcome, "failed": True, "reason": error}
            log.exception("agent run %s could not capture its post snapshot", self.run_id)

        try:
            manuscript_snapshot = quality.build_manuscript_snapshot(
                board,
                source_snapshot_id=post_snapshot["id"] if post_snapshot else "",
            )
        except Exception as exc:  # immutable review evidence is best-effort on failure
            manuscript_snapshot = {
                "schema_version": quality.MANUSCRIPT_SNAPSHOT_SCHEMA_VERSION,
                "source_snapshot_id": post_snapshot["id"] if post_snapshot else "",
                "sections": [],
                "manuscript_fingerprint": "",
                "error": f"{type(exc).__name__}: {exc}",
            }
            self.warnings.append(
                f"immutable manuscript evidence could not be built: {exc}"
            )
            log.exception("agent run %s manuscript evidence failed", self.run_id)

        try:
            citation_paper_ids = board.extra.get("citation_paper_ids") or []
            citation_papers = papers_store.get_many(citation_paper_ids)
            quality_report = quality.build_quality_report(
                board,
                citation_papers=citation_papers,
                expect_translation=self.config.enable_translation,
                run_status=status,
            )
        except Exception as exc:  # quality evidence must not destroy completed prose
            self.warnings.append(f"automatic quality report could not be built: {exc}")
            quality_report = {
                "schema_version": quality.QUALITY_REPORT_SCHEMA_VERSION,
                "generated_at": "",
                "run_status": status,
                "gate": "unavailable",
                "summary": {"pass": 0, "warn": 0, "fail": 0, "not_run": 1},
                "checks": [],
                "acceptance": {
                    "automatic_gate": "unavailable",
                    "human_review_required": True,
                    "semantic_grounding_verified": False,
                    "latest_human_decision": "unreviewed",
                },
                "limitations": [f"quality report generation failed: {type(exc).__name__}"],
            }
            log.exception("agent run %s quality report failed", self.run_id)

        audit_totals = runs_store.get_run(self.run_id, with_steps=False) or {}
        accounted_tokens_in = int(audit_totals.get("tokens_in") or self.tokens_in)
        accounted_tokens_out = int(audit_totals.get("tokens_out") or self.tokens_out)
        result = {
            **outcome,
            "pipeline": self.pipeline,
            "sections_written": written["sections"],
            "document_id": written["document_id"],
            "tokens_in": accounted_tokens_in,
            "tokens_out": accounted_tokens_out,
            "steps": self.step_index,
            "warnings": self.warnings,
            "snapshots": {
                "before": pre_snapshot["id"],
                "after": post_snapshot["id"] if post_snapshot else "",
            },
            "failure": self.failures[0] if self.failures else {},
            "failures": self.failures,
            "recovery": {
                "strategy": "partial_work_preserved",
                "completed_steps_kept": True,
                "partial_step_output_audited": True,
                "restore_snapshot_id": pre_snapshot["id"],
                "compare_snapshot_id": post_snapshot["id"] if post_snapshot else "current",
                "retryable": any(bool(item.get("retryable")) for item in self.failures),
            },
            "blackboard": board.snapshot(),
            "review_manuscript": manuscript_snapshot,
            "quality_report": quality_report,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        runs_store.finish_run(
            self.run_id, status=status, result=result, error=error
        )
        events.publish(
            events.AGENT_RUN_DONE if status == "done" else events.AGENT_RUN_FAILED,
            {"runId": self.run_id, "status": status, "error": error,
             "sectionsWritten": written["sections"],
             "tokensIn": accounted_tokens_in, "tokensOut": accounted_tokens_out,
             **(self.failures[0] if self.failures else {})},
            project_id=self.project_id,
            job_id=self.job.job_id if self.job else None,
        )
        if status == "failed":
            log.error("run %s failed after %s steps: %s", self.run_id,
                      self.step_index, error)
        elif status == "done":
            log.info(
                "run %s (%s) completed: %s sections, %s steps, %s tokens in / "
                "%s out, %.1fs",
                self.run_id, self.pipeline, written["sections"], self.step_index,
                accounted_tokens_in, accounted_tokens_out,
                time.perf_counter() - started,
            )
        if status in {"failed", "cancelled"}:
            # The run and snapshots are durable before the job boundary sees the
            # exception. This keeps Run/Step/Job status consistent and lets the
            # desktop refresh the persisted diagnosis after waitForJob rejects.
            assert failure_exc is not None
            raise failure_exc
        return result

    def _load_skills(self) -> str:
        """Render the selected skills into a prompt fragment.

        Failures are non-fatal: a broken skill should not block writing, so it is
        reported as a warning and the run proceeds without it.
        """
        ids = self.skill_ids or self.config.skills
        if not ids:
            return ""
        try:
            from ..skills import runner as skills_runner

            text, used, problems = skills_runner.render_for_prompt(ids)
            self.warnings.extend(problems)
            if used:
                log.info("run %s using skills: %s", self.run_id, ", ".join(used))
            return text
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"skills could not be loaded: {exc}")
            return ""

    # ------------------------------------------------------------ pipelines
    async def _run_full_auto(self, board: Blackboard) -> dict[str, Any]:
        """The complete chain, from idea to harmonised manuscript."""
        if self.job is not None:
            self.job.progress(0.03, "planning the paper")
        await self._step(self._make(PlannerAgent), board)

        if board.papers:
            if self.job is not None:
                self.job.progress(0.10, "reading the literature")
            await self._step(self._make(ReaderAgent), board)
            await self._step(self._make(SynthesiserAgent), board)

        if self.job is not None:
            self.job.progress(0.28, "validating research gaps")
        await self._step(self._make(IdeatorAgent), board)

        if self.job is not None:
            self.job.progress(0.34, "building the outline")
        await self._step(self._make(OutlinerAgent), board)
        if not board.outline:
            # Fall back to the plan's own section list rather than failing: the
            # outliner occasionally returns prose instead of the JSON schema.
            board.outline = self._outline_from_plan(board)
            if board.outline:
                self.warnings.append(
                    "the outliner returned no usable sections; the plan's section "
                    "list was used instead"
                )
        if not board.outline:
            raise ValidationError(
                "no outline could be produced, so there is nothing to draft"
            )

        drafted = await self._draft_sections(
            board, [s["key"] for s in board.outline], progress_from=0.40,
            progress_to=0.82,
        )

        if self.job is not None:
            self.job.progress(0.86, "verifying citations")
        await self._step(self._make(CitationAgent), board)

        if len(board.sections) >= 2:
            if self.job is not None:
                self.job.progress(0.90, "harmonising the manuscript")
            await self._step(self._make(PolisherAgent), board)

        if self.config.enable_translation:
            await self._translate(board, list(board.sections), progress_from=0.93)

        return {"mode": "full_auto", "drafted": drafted,
                "outline": [s["key"] for s in board.outline]}

    def _outline_from_plan(self, board: Blackboard) -> list[dict[str, Any]]:
        from .roles import _normalise_outline, _keys_for

        sections = (board.plan or {}).get("sections") or []
        return _normalise_outline(
            sections, board, _keys_for(board, board.papers)
        )

    async def _run_sections(self, board: Blackboard) -> dict[str, Any]:
        """Draft or redraft specific sections.

        Missing prerequisites are rebuilt only as far as needed: if the project
        already has an outline (from a previous run or from the user's own
        structure) no planning happens, which keeps a single-section redraft to
        two or three model calls instead of a dozen.
        """
        wanted = self.config.section_keys or [s["key"] for s in board.outline]
        if not wanted:
            if self.job is not None:
                self.job.progress(0.05, "no outline yet - planning first")
            await self._step(self._make(PlannerAgent), board)
            await self._step(self._make(OutlinerAgent), board)
            if not board.outline:
                board.outline = self._outline_from_plan(board)
            wanted = [s["key"] for s in board.outline]
        if not wanted:
            raise ValidationError(
                "no sections to draft. Provide section_keys, or create the "
                "document structure first."
            )

        unknown = [k for k in wanted if not any(s["key"] == k for s in board.outline)]
        if unknown:
            raise ValidationError(
                f"unknown section key(s): {', '.join(unknown)}. Available: "
                f"{', '.join(s['key'] for s in board.outline)}"
            )

        # Notes and themes materially improve draft quality; build them if the
        # blackboard has none and there is literature to read.
        if board.papers and not board.paper_notes:
            if self.job is not None:
                self.job.progress(0.12, "reading the literature")
            await self._step(self._make(ReaderAgent), board)
        if board.papers and not board.themes:
            await self._step(self._make(SynthesiserAgent), board)

        drafted = await self._draft_sections(
            board, wanted, progress_from=0.35, progress_to=0.90
        )
        if self.config.enable_translation:
            await self._translate(board, wanted, progress_from=0.92)
        return {"mode": "section", "requested": wanted, "drafted": drafted}

    async def _run_stitch(self, board: Blackboard) -> dict[str, Any]:
        """Join independently written sections into one coherent paper."""
        if not board.sections:
            raise ValidationError(
                "there are no drafted sections to stitch together yet"
            )
        if self.job is not None:
            self.job.progress(0.15, "verifying citations across the manuscript")
        await self._step(self._make(CitationAgent), board)

        if self.job is not None:
            self.job.progress(0.45, "harmonising terminology and transitions")
        polish = await self._step(self._make(PolisherAgent), board)

        # A stitch pass is also the right moment to catch section-level problems
        # that only became visible once neighbours existed.
        if self.config.enable_critique:
            targets = self.config.section_keys or list(board.sections)
            for index, key in enumerate(targets):
                if self.job is not None:
                    self.job.progress(
                        0.55 + 0.3 * (index / max(1, len(targets))),
                        f"reviewing {key} in context",
                    )
                await self._step(self._make(CriticAgent, section_key=key), board)
                if board.critiques.get(key):
                    await self._step(self._make(ReviserAgent, section_key=key), board)

        if self.config.enable_translation:
            await self._translate(board, list(board.sections), progress_from=0.9)
        return {
            "mode": "stitch",
            "sections": list(board.sections),
            "polish": polish.output if polish else {},
            "citation_report": board.extra.get("citation_report", {}),
        }

    async def _run_custom(self, board: Blackboard) -> dict[str, Any]:
        """Run an explicit list of roles, in order."""
        if not self.custom_roles:
            raise ValidationError("custom pipeline requires a list of role names")
        unknown = [r for r in self.custom_roles if r not in ALL_ROLES]
        if unknown:
            raise ValidationError(
                f"unknown role(s): {', '.join(unknown)}. Available: "
                f"{', '.join(sorted(ALL_ROLES))}"
            )
        executed: list[str] = []
        per_section_roles = {"writer", "critic", "reviser", "translator"}
        for index, role_name in enumerate(self.custom_roles):
            if self.job is not None:
                self.job.progress(
                    0.05 + 0.9 * (index / max(1, len(self.custom_roles))),
                    f"running {role_name}",
                )
            role_class = ALL_ROLES[role_name]
            if role_name in per_section_roles:
                keys = self.config.section_keys or [s["key"] for s in board.outline]
                for key in keys:
                    await self._step(self._make(role_class, section_key=key), board)
                    executed.append(f"{role_name}:{key}")
            else:
                await self._step(self._make(role_class), board)
                executed.append(role_name)
        return {"mode": "custom", "executed": executed}

    # -------------------------------------------------------------- helpers
    async def _draft_sections(
        self,
        board: Blackboard,
        keys: list[str],
        *,
        progress_from: float,
        progress_to: float,
    ) -> list[dict[str, Any]]:
        """Draft each section, then optionally review and revise it.

        Sections are drafted **sequentially**, not concurrently, for two
        reasons: each draft is told what the previous ones said so it can avoid
        repeating them, and streaming several sections at once would interleave
        deltas in the editor. The reader agent is where concurrency pays off,
        and it already uses it.
        """
        drafted: list[dict[str, Any]] = []
        span = max(0.01, progress_to - progress_from)
        for index, key in enumerate(keys):
            fraction = progress_from + span * (index / max(1, len(keys)))
            if self.job is not None:
                self.job.progress(fraction, f"drafting {key} ({index + 1}/{len(keys)})")
            try:
                result = await self._step(self._make(WriterAgent, section_key=key), board)
            except BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the other sections
                self.warnings.append(f"section '{key}' could not be drafted: {exc}")
                self._remember_failure(exc, context=f"drafting section '{key}'")
                log.warning("writer failed on section %s: %s", key, exc)
                continue
            entry = {"key": key, "words": (result.output or {}).get("words", 0)}

            if self.config.enable_critique:
                try:
                    await self._step(self._make(CriticAgent, section_key=key), board)
                    issues = board.critiques.get(key) or []
                    actionable = [
                        i for i in issues if i.get("severity") in ("high", "medium")
                    ]
                    entry["issues"] = len(issues)
                    if actionable:
                        revised = await self._step(
                            self._make(ReviserAgent, section_key=key), board
                        )
                        entry["revised"] = bool((revised.output or {}).get("revised"))
                        entry["words"] = (
                            (revised.output or {}).get("words") or entry["words"]
                        )
                except BudgetExceeded:
                    raise
                except Exception as exc:  # noqa: BLE001 - the draft still stands
                    self.warnings.append(f"review of '{key}' failed: {exc}")
                    self._remember_failure(exc, context=f"reviewing section '{key}'")
            drafted.append(entry)
        return drafted

    async def _translate(
        self, board: Blackboard, keys: list[str], *, progress_from: float
    ) -> None:
        target = "zh" if self.config.language == "en" else "en"
        span = max(0.01, 0.99 - progress_from)
        for index, key in enumerate(keys):
            if not board.sections.get(key, "").strip():
                continue
            if self.job is not None:
                self.job.progress(
                    progress_from + span * (index / max(1, len(keys))),
                    f"translating {key} to {target}",
                )
            try:
                await self._step(
                    self._make(TranslatorAgent, section_key=key,
                               target_language=target),
                    board,
                )
            except BudgetExceeded:
                raise
            except Exception as exc:  # noqa: BLE001
                self.warnings.append(f"translation of '{key}' failed: {exc}")
                self._remember_failure(exc, context=f"translating section '{key}'")

    def _persist(self, board: Blackboard) -> dict[str, Any]:
        """Write the blackboard's sections into the manuscript document.

        Idempotent per key: an existing section is updated, a new one created.
        The on-disk mirror is flushed so git sees the change.
        """
        document_id = board.extra.get("document_id")
        if not document_id:
            document_id = documents_store.primary_document(self.project_id).id
        # Do not spend completed LLM work by writing it over an external file
        # edit. This preflight happens before the first DB mutation.
        documents_store.ensure_sync_safe(document_id, "flush")
        written = 0
        outline_by_key = {s["key"]: s for s in board.outline}
        cached_keys = board.extra.get("citation_keys")
        key_by_paper = cached_keys if isinstance(cached_keys, dict) else {}
        paper_by_key = {
            str(citation_key): str(paper_id)
            for paper_id, citation_key in key_by_paper.items()
        }

        outline_order = [
            str(section.get("key") or "") for section in board.outline
            if str(section.get("key") or "") in board.modified_section_keys
        ]
        remaining = sorted(board.modified_section_keys.difference(outline_order))
        for key in [*outline_order, *remaining]:
            text = board.sections.get(key, "")
            if not text.strip():
                continue
            # Final text is authoritative. Reviser/Polisher may add, remove or
            # move markers after the Writer populated ``board.citations``.
            cited_paper_ids = list(
                dict.fromkeys(
                    paper_by_key[marker]
                    for marker in citations_module.find_markers(text)
                    if marker in paper_by_key
                )
            )
            board.citations[key] = cited_paper_ids
            meta = outline_by_key.get(key, {})
            existing = documents_store.get_section_by_key(document_id, key)
            fields: dict[str, Any] = {
                "content": text,
                "status": "drafted",
                "cited_paper_ids": cited_paper_ids,
            }
            translation = board.translations.get(key)
            if translation:
                fields["content_zh"] = translation
            if existing is None:
                documents_store.create_section(
                    document_id,
                    key=key,
                    title=str(meta.get("title") or key.replace("-", " ").title()),
                    ordering=int(meta.get("ordering") or 0) or None,
                    level=int(meta.get("level") or 1),
                    guidance=str(meta.get("guidance") or ""),
                    target_words=int(meta.get("target_words") or 0),
                    content=text,
                    content_zh=translation or "",
                    status="drafted",
                )
                created = documents_store.get_section_by_key(document_id, key)
                if created is not None:
                    documents_store.update_section(
                        created.id, cited_paper_ids=cited_paper_ids
                    )
            else:
                documents_store.update_section(existing.id, **fields)
            written += 1

        if written:
            try:
                documents_store.flush_document_to_disk(document_id)
            except OSError as exc:
                self.warnings.append(f"could not write manuscript files: {exc}")
            events.publish(
                events.DOCUMENT_UPDATED,
                {"documentId": document_id, "sections": written},
                project_id=self.project_id,
            )
        return {"sections": written, "document_id": document_id}


# --------------------------------------------------------------- entry points


def run_pipeline_sync(
    *,
    project_id: str,
    pipeline: str = "full_auto",
    config_overrides: dict[str, Any] | None = None,
    analysis_id: str = "",
    paper_ids: list[str] | None = None,
    skill_ids: list[str] | None = None,
    custom_roles: list[str] | None = None,
    run_id: str = "",
    job: JobContext | None = None,
) -> dict[str, Any]:
    """Blocking pipeline execution, for the job runner's worker threads."""
    project = projects_store.require(project_id)
    config = build_run_config(project, config_overrides)
    resolved_run_id = run_id or runs_store.create_run(
        project_id=project_id,
        pipeline=pipeline,
        mode=config.language,
        request={
            "pipeline": pipeline,
            **{k: v for k, v in config.__dict__.items() if not k.startswith("_")},
            "analysis_id": analysis_id,
            "paper_ids": paper_ids or [],
            "skill_ids": skill_ids or [],
            "custom_roles": custom_roles or [],
        },
    )
    orchestrator = Orchestrator(
        project_id=project_id,
        pipeline=pipeline,
        config=config,
        run_id=resolved_run_id,
        job=job,
        analysis_id=analysis_id,
        paper_ids=paper_ids,
        skill_ids=skill_ids,
        custom_roles=custom_roles,
    )
    return asyncio.run(orchestrator.run())


def submit_run(
    *,
    project_id: str,
    pipeline: str = "full_auto",
    config_overrides: dict[str, Any] | None = None,
    analysis_id: str = "",
    paper_ids: list[str] | None = None,
    skill_ids: list[str] | None = None,
    custom_roles: list[str] | None = None,
) -> dict[str, str]:
    """Queue an agent run. Returns ``{job_id, run_id}``.

    The run row is created here rather than inside the worker so the caller gets
    an id it can immediately subscribe to.
    """
    project = projects_store.require(project_id)
    config = build_run_config(project, config_overrides)
    run_id = runs_store.create_run(
        project_id=project_id,
        pipeline=pipeline,
        mode=config.language,
        request={
            "pipeline": pipeline,
            **{k: v for k, v in config.__dict__.items() if not k.startswith("_")},
            "analysis_id": analysis_id,
            "paper_ids": paper_ids or [],
            "skill_ids": skill_ids or [],
            "custom_roles": custom_roles or [],
        },
    )
    handle = manager.submit(
        f"agent:{pipeline}",
        lambda ctx: run_pipeline_sync(
            project_id=project_id, pipeline=pipeline,
            config_overrides=config_overrides, analysis_id=analysis_id,
            paper_ids=paper_ids, skill_ids=skill_ids, custom_roles=custom_roles,
            run_id=run_id, job=ctx,
        ),
        payload={"pipeline": pipeline, "run_id": run_id},
        project_id=project_id,
    )
    return {"job_id": handle.id, "run_id": run_id}


def describe_pipelines() -> list[dict[str, Any]]:
    """Pipeline catalogue for the UI."""
    return [
        {
            "id": "full_auto",
            "name": "Write the whole paper",
            "name_zh": "一次生成全文",
            "description": "Plan, read the literature, validate gaps, outline, "
                           "draft every section, review, revise, check citations "
                           "and harmonise - in one run.",
            "description_zh": "规划、阅读文献、验证缺口、生成大纲、逐节撰写、"
                              "审阅修订、核查引用并统一全文，一次完成。",
            "steps": ["planner", "reader", "synthesiser", "ideator", "outliner",
                      "writer x N", "critic + reviser x N", "citation_checker",
                      "polisher"],
            "typical_calls": "12-40 depending on section count",
        },
        {
            "id": "section",
            "name": "Write specific sections",
            "name_zh": "撰写指定章节",
            "description": "Draft or redraft only the sections you choose, "
                           "reusing the existing outline and notes.",
            "description_zh": "仅撰写或重写选定章节，复用已有大纲与笔记。",
            "steps": ["(planner + outliner if no outline)", "reader",
                      "synthesiser", "writer", "critic", "reviser"],
            "typical_calls": "2-6 per section",
        },
        {
            "id": "stitch",
            "name": "Join the parts together",
            "name_zh": "拼接成文",
            "description": "Take sections written separately and make them one "
                           "paper: consistent terminology, working transitions, "
                           "verified citations.",
            "description_zh": "把分开写好的章节整合为一篇论文：统一术语、衔接过渡、"
                              "核查引用。",
            "steps": ["citation_checker", "polisher", "critic + reviser x N"],
            "typical_calls": "2 + 2 per section",
        },
        {
            "id": "custom",
            "name": "Custom role sequence",
            "name_zh": "自定义流程",
            "description": "Run an explicit list of agent roles in order. Used by "
                           "skills and for experimentation.",
            "description_zh": "按顺序执行指定的 Agent 角色，供 skill 与实验使用。",
            "steps": ["(your choice)"],
            "typical_calls": "varies",
        },
    ]

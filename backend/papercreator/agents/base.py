"""Agent contract and shared context.

An *agent* here is a narrow, single-purpose LLM role with a defined input and
output contract - not an autonomous loop. That choice is deliberate: paper
writing has a natural division of labour (plan, read, synthesise, outline, draft,
critique, cite, translate), each step benefits from a different prompt and a
different amount of context, and a bounded role is auditable. The user asked to
be able to run "all at once" or "part by part"; both are just different
compositions of the same roles.

Shared state lives in the :class:`Blackboard`, which is the only channel between
agents. No agent calls another directly - the orchestrator sequences them. That
keeps every step independently re-runnable, which is what makes "redo just the
methods section" possible.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..core import events
from ..core.errors import ConfigurationError, error_diagnostics
from ..core.jobs import JobCancelled, JobContext
from ..core.logging_setup import get_logger
from ..core.models import Paper, ProjectModel
from ..llm import client as llm_client
from ..store import runs as runs_store

log = get_logger(__name__)


@dataclass
class Blackboard:
    """Shared, append-mostly state for one agent run.

    Every agent reads what it needs and writes its product here. The keys are a
    contract between agents, documented in ``docs/systems/agent_system.md``:

    ``plan``            - the planner's output (audience, contribution, sections)
    ``paper_notes``     - {paper_id: {summary, method, findings, relevance}}
    ``themes``          - grouped literature themes with their supporting papers
    ``gap_analysis``    - the ideator's reading of the landscape gaps
    ``outline``         - [{key, title, guidance, target_words, paper_ids}]
    ``sections``        - {section_key: drafted text}
    ``critiques``       - {section_key: [issues]}
    ``citations``       - {section_key: [paper_id]} actually cited
    ``translations``    - {section_key: zh text}
    """

    project: ProjectModel
    papers: list[Paper] = field(default_factory=list)
    analysis: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    paper_notes: dict[str, dict[str, Any]] = field(default_factory=dict)
    themes: list[dict[str, Any]] = field(default_factory=list)
    gap_analysis: dict[str, Any] = field(default_factory=dict)
    outline: list[dict[str, Any]] = field(default_factory=list)
    sections: dict[str, str] = field(default_factory=dict)
    critiques: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    citations: dict[str, list[str]] = field(default_factory=dict)
    translations: dict[str, str] = field(default_factory=dict)
    # Only these keys may be persisted at the end of a run. Existing manuscript
    # sections are loaded for context, but reading them must not turn into a
    # write or inflate ``sections_written``.
    modified_section_keys: set[str] = field(default_factory=set)
    # Free-form scratch space for skills and custom pipelines.
    extra: dict[str, Any] = field(default_factory=dict)

    def papers_by_id(self) -> dict[str, Paper]:
        return {p.id: p for p in self.papers}

    def top_papers(self, limit: int = 40) -> list[Paper]:
        """Highest-signal papers first, for prompts that cannot fit everything.

        Ranking: user's own work first (it defines the contribution), then
        papers with notes (already judged relevant), then by citation count and
        recency. This ordering is what decides *which* literature the model sees
        when a corpus does not fit the context window, so it matters.
        """
        def key(paper: Paper) -> tuple:
            return (
                0 if paper.origin in ("idea", "own_paper") else 1,
                0 if paper.id in self.paper_notes else 1,
                -paper.citation_count,
                -(paper.year or 0),
            )

        return sorted(self.papers, key=key)[:limit]

    def snapshot(self) -> dict[str, Any]:
        """Serialisable state, stored on the run for auditing."""
        return {
            "plan": self.plan,
            "themes": self.themes,
            "gap_analysis": self.gap_analysis,
            "outline": self.outline,
            "section_keys": sorted(self.sections),
            "note_count": len(self.paper_notes),
            "critique_count": sum(len(v) for v in self.critiques.values()),
            "translated": sorted(self.translations),
            "modified_sections": sorted(self.modified_section_keys),
        }


@dataclass
class AgentResult:
    agent: str
    output: Any
    text: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: int = 0
    model: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class RunConfig:
    """Per-run knobs. Defaults come from settings; the UI can override each."""

    model: str = ""                    # "" = role default
    fast_model: str = ""               # cheap model for notes/classification
    temperature: float | None = None
    max_tokens: int = 4000
    language: str = "en"
    bilingual: bool = False
    citation_style: str = "ieee"
    target_words: int = 6000
    max_papers_in_context: int = 40
    max_notes: int = 25                # how many papers get an individual read
    enable_critique: bool = True
    enable_translation: bool = False
    skills: list[str] = field(default_factory=list)
    token_budget: int = 400_000
    # Section keys to work on; empty = whatever the outline produces.
    section_keys: list[str] = field(default_factory=list)


class Agent(ABC):
    """One LLM role.

    Subclasses implement :meth:`run` and declare ``name``/``title``. Everything
    else - step recording, event emission, budget checks, cancellation - is
    handled by :meth:`execute`, so a new role is a prompt plus a parser.
    """

    name: str = ""
    title: str = ""
    title_zh: str = ""
    description: str = ""
    # Which blackboard keys must be populated before this agent can run. The
    # orchestrator validates these, producing a clear error instead of a prompt
    # built from empty state.
    requires: tuple[str, ...] = ()
    # Prefer the cheap model: true for high-volume, low-judgement work.
    prefers_fast_model: bool = False

    def __init__(
        self,
        *,
        config: RunConfig,
        run_id: str = "",
        job: JobContext | None = None,
        skills_text: str = "",
    ) -> None:
        self.config = config
        self.run_id = run_id
        self.job = job
        self.skills_text = skills_text
        self._step_index = 0
        self._active_step_id = ""

    # ------------------------------------------------------------ interface
    @abstractmethod
    async def run(self, board: Blackboard) -> AgentResult:
        """Do the work. Read from and write to ``board``."""

    # -------------------------------------------------------------- helpers
    @property
    def model(self) -> str:
        if self.prefers_fast_model and self.config.fast_model:
            return self.config.fast_model
        return self.config.model

    def check_requirements(self, board: Blackboard) -> None:
        missing = [
            key for key in self.requires
            if not getattr(board, key, None)
        ]
        if missing:
            raise ConfigurationError(
                f"agent '{self.name}' needs {', '.join(missing)} on the "
                f"blackboard but they are empty - an earlier step did not "
                f"produce its output",
                details={"agent": self.name, "missing": missing},
            )

    async def execute(self, board: Blackboard, *, ordering: int = 0) -> AgentResult:
        """Wrapper adding recording, events, cancellation and error capture."""
        if self.job is not None:
            self.job.raise_if_cancelled()
        self.check_requirements(board)

        step_id = runs_store.create_step(
            self.run_id, agent=self.name, title=self.title, ordering=ordering,
            model=self.model or "(role default)",
        ) if self.run_id else ""
        events.publish(
            events.AGENT_STEP_STARTED,
            {"agent": self.name, "title": self.title, "ordering": ordering,
             "stepId": step_id},
            project_id=board.project.id,
            job_id=self.job.job_id if self.job else None,
        )
        started = time.perf_counter()
        self._active_step_id = step_id
        try:
            result = await self.run(board)
        except Exception as exc:  # noqa: BLE001 - recorded then re-raised
            duration = int((time.perf_counter() - started) * 1000)
            cancelled = isinstance(exc, JobCancelled)
            failure = (
                {
                    "outcome": "cancelled",
                    "error_code": "cancelled",
                    "retryable": False,
                    "http_status": None,
                    "retry_after_s": None,
                    "hint": "",
                    "provider": "",
                    "model": self.model,
                    "message": "cancelled by user",
                    "error_type": type(exc).__name__,
                    **dict(getattr(exc, "details", {}) or {}),
                }
                if cancelled else error_diagnostics(exc, model=self.model)
            )
            partial_output = str(failure.pop("partial_output", "") or "")
            partial_tokens_in = int(failure.get("partial_tokens_in") or 0)
            partial_tokens_out = int(failure.get("partial_tokens_out") or 0)
            if step_id:
                runs_store.finish_step(
                    step_id, status="cancelled" if cancelled else "failed",
                    output=partial_output[:200_000],
                    error=f"{type(exc).__name__}: {exc}",
                    tokens_in=partial_tokens_in, tokens_out=partial_tokens_out,
                    duration_ms=duration,
                    meta={"failure": failure, "partial_output_kept": bool(partial_output)},
                )
            events.publish(
                events.AGENT_STEP_DONE,
                {"agent": self.name, "stepId": step_id,
                 "status": "cancelled" if cancelled else "failed",
                 "error": str(exc), **failure},
                project_id=board.project.id,
                job_id=self.job.job_id if self.job else None,
            )
            raise
        finally:
            self._active_step_id = ""

        result.duration_ms = int((time.perf_counter() - started) * 1000)
        if step_id:
            runs_store.finish_step(
                step_id, status="done", output=result.text[:200_000],
                tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                duration_ms=result.duration_ms,
                meta={"warnings": result.warnings, "model": result.model},
            )
        events.publish(
            events.AGENT_STEP_DONE,
            {"agent": self.name, "stepId": step_id, "status": "done",
             "tokensIn": result.tokens_in, "tokensOut": result.tokens_out,
             "durationMs": result.duration_ms,
             "preview": result.text[:400]},
            project_id=board.project.id,
            job_id=self.job.job_id if self.job else None,
        )
        return result

    async def ask(
        self,
        prompt: str,
        *,
        system: str = "",
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> tuple[Any, int, int, str]:
        """Call the LLM. Returns ``(payload, tokens_in, tokens_out, model)``.

        ``json_mode`` routes through :func:`llm.client.complete_json`, which
        repairs the usual JSON damage; otherwise the raw text is returned.
        """
        if self.job is not None:
            self.job.raise_if_cancelled()
        full_system = system
        if self.skills_text:
            # Skills are appended to the system prompt rather than the user turn
            # so they read as standing instructions, and so they survive any
            # trimming of the (much larger) user content.
            full_system = f"{system}\n\n{self.skills_text}".strip()
        self._record_prompt(full_system, prompt)

        if json_mode:
            payload = await llm_client.complete_json(
                prompt,
                system=full_system,
                model=self.model,
                max_tokens=max_tokens or self.config.max_tokens,
                temperature=0.1 if temperature is None else temperature,
                purpose=self.name,
                run_id=self.run_id,
            )
            # complete_json does not surface the Completion, so usage is
            # attributed by the client and estimated here for the step record.
            from ..core.util import estimate_tokens
            import json as json_module

            text = json_module.dumps(payload, ensure_ascii=False)
            return (
                payload,
                estimate_tokens(prompt) + estimate_tokens(full_system),
                estimate_tokens(text),
                self.model or "(role default)",
            )

        completion = await llm_client.complete(
            prompt,
            system=full_system,
            model=self.model,
            max_tokens=max_tokens or self.config.max_tokens,
            temperature=(
                self.config.temperature if temperature is None else temperature
            ),
            purpose=self.name,
            run_id=self.run_id,
        )
        return (
            completion.text,
            completion.usage.prompt_tokens,
            completion.usage.completion_tokens,
            completion.model,
        )

    async def ask_streaming(
        self,
        prompt: str,
        *,
        system: str = "",
        section_key: str = "",
        max_tokens: int | None = None,
    ) -> tuple[str, int, int, str]:
        """Stream a completion, emitting deltas so the editor fills live.

        Used for section drafting, where a 1000-word section takes long enough
        that watching it arrive materially changes how the tool feels.
        """
        if self.job is not None:
            self.job.raise_if_cancelled()
        full_system = (
            f"{system}\n\n{self.skills_text}".strip() if self.skills_text else system
        )
        self._record_prompt(full_system, prompt)
        collected: list[str] = []
        tokens_in = tokens_out = 0
        model = self.model or "(role default)"
        async for chunk in llm_client.stream(
            prompt,
            system=full_system,
            model=self.model,
            max_tokens=max_tokens or self.config.max_tokens,
            temperature=self.config.temperature,
            purpose=self.name,
            run_id=self.run_id,
        ):
            if chunk.delta:
                collected.append(chunk.delta)
                events.publish(
                    events.AGENT_STEP_DELTA,
                    {"agent": self.name, "sectionKey": section_key,
                     "delta": chunk.delta},
                    project_id="",
                    job_id=self.job.job_id if self.job else None,
                )
            if chunk.done and chunk.completion is not None:
                tokens_in = chunk.completion.usage.prompt_tokens
                tokens_out = chunk.completion.usage.completion_tokens
                model = chunk.completion.model
            if self.job is not None and self.job.cancelled:
                cancelled = JobCancelled()
                cancelled.details = {
                    "partial_output": "".join(collected),
                    "partial_output_chars": sum(len(part) for part in collected),
                    "partial_tokens_in": tokens_in,
                    "partial_tokens_out": tokens_out,
                }
                raise cancelled
        return "".join(collected), tokens_in, tokens_out, model

    def _record_prompt(self, system: str, prompt: str) -> None:
        """Persist the exact two-message request for the step audit view."""
        if not self._active_step_id:
            return
        transcript = "SYSTEM\n" + (system or "(empty)")
        transcript += "\n\nUSER\n" + (prompt or "(empty)")
        runs_store.append_step_prompt(self._active_step_id, transcript)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "title_zh": self.title_zh,
            "description": self.description,
            "requires": list(self.requires),
            "prefers_fast_model": self.prefers_fast_model,
        }

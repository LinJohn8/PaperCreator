"""The agent roles.

Each class is one narrow LLM step with a defined blackboard contract. The
division mirrors how a paper actually gets written, so a user can run the whole
chain or re-run one link:

======================  ==========================  ==========================
role                    reads                       writes
======================  ==========================  ==========================
PlannerAgent            project, papers, analysis   plan
ReaderAgent             papers                      paper_notes
SynthesiserAgent        papers, paper_notes         themes
IdeatorAgent            analysis, themes            gap_analysis
OutlinerAgent           plan, themes, gap_analysis  outline
WriterAgent             outline, themes, notes      sections
CriticAgent             sections, outline           critiques
ReviserAgent            sections, critiques         sections (replaced)
CitationAgent           sections                    citations
TranslatorAgent         sections                    translations
PolisherAgent           sections                    sections (harmonised)
======================  ==========================  ==========================
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ..core.logging_setup import get_logger
from ..core.models import Paper
from ..core.util import slugify, truncate, word_count
from . import prompts
from .base import Agent, AgentResult, Blackboard

log = get_logger(__name__)


def _keys_for(board: Blackboard, papers: list[Paper]) -> dict[str, str]:
    """Citation keys, computed once per run and cached on the blackboard.

    Stability matters: a key that changes between the writer and the citation
    checker would invalidate every marker in the drafted text.
    """
    cached = board.extra.get("citation_keys")
    if isinstance(cached, dict) and cached:
        missing = [p for p in papers if p.id not in cached]
        if not missing:
            return cached
    keys = prompts.build_citation_keys(board.papers or papers)
    board.extra["citation_keys"] = keys
    return keys


class PlannerAgent(Agent):
    name = "planner"
    title = "Plan the paper"
    title_zh = "规划论文"
    description = "Decides paper type, contribution claim, and section structure."

    async def run(self, board: Blackboard) -> AgentResult:
        papers = board.top_papers(min(self.config.max_papers_in_context, 30))
        keys = _keys_for(board, papers)
        prompt = "\n\n".join(filter(None, [
            prompts.format_project_context(board.project),
            prompts.format_analysis_context(board.analysis),
            f"RETRIEVED LITERATURE ({len(board.papers)} papers total, "
            f"{len(papers)} shown):\n"
            + prompts.format_paper_list(
                papers, keys, abstract_chars=260, notes=board.paper_notes
            ),
            f"TARGET LENGTH: about {self.config.target_words} words total.",
            f"OUTPUT LANGUAGE: {self.config.language}.",
        ]))
        payload, tin, tout, model = await self.ask(
            prompt, system=prompts.PLANNER, json_mode=True, max_tokens=2500
        )
        if not isinstance(payload, dict):
            payload = {"raw": payload}
        board.plan = payload
        contribution = payload.get("contribution") or ""
        log.info("planner: %s", truncate(str(contribution), 120))
        return AgentResult(
            agent=self.name, output=payload,
            text=str(payload.get("contribution", "")) + "\n" + str(
                [s.get("key") for s in payload.get("sections", [])]
            ),
            tokens_in=tin, tokens_out=tout, model=model,
        )


class ReaderAgent(Agent):
    """Reads individual papers into structured notes.

    Runs the highest-value papers concurrently with a small semaphore: notes are
    independent of each other, so serialising them would waste most of the wall
    clock, but firing 40 requests at once trips provider rate limits.
    """

    name = "reader"
    title = "Read the key papers"
    title_zh = "阅读关键论文"
    description = "Extracts structured notes (problem, method, findings) per paper."
    prefers_fast_model = True

    max_concurrency = 4

    async def run(self, board: Blackboard) -> AgentResult:
        candidates = [
            p for p in board.top_papers(self.config.max_notes)
            if p.abstract and p.id not in board.paper_notes
        ]
        if not candidates:
            return AgentResult(
                agent=self.name, output={}, text="no papers with abstracts to read",
                warnings=["no unread papers with abstracts"],
            )
        semaphore = asyncio.Semaphore(self.max_concurrency)
        total_in = total_out = 0
        model_used = ""
        failures: list[str] = []

        async def read_one(paper: Paper) -> None:
            nonlocal total_in, total_out, model_used
            async with semaphore:
                prompt = (
                    f"AUTHOR'S IDEA (judge relevance against this):\n"
                    f"{board.project.idea or board.project.title}\n\n"
                    f"PAPER\nTitle: {paper.title}\n"
                    f"Authors: {', '.join(paper.author_names(5))}\n"
                    f"Year: {paper.year or 'unknown'}  Venue: {paper.venue}\n"
                    f"Abstract: {truncate(paper.abstract, 2600)}"
                )
                try:
                    payload, tin, tout, model = await self.ask(
                        prompt, system=prompts.READER, json_mode=True, max_tokens=900
                    )
                except Exception as exc:  # noqa: BLE001 - one paper must not stop the batch
                    failures.append(f"{paper.id}: {exc}")
                    return
                if isinstance(payload, dict):
                    board.paper_notes[paper.id] = payload
                total_in += tin
                total_out += tout
                model_used = model or model_used
                if self.job is not None:
                    done = len(board.paper_notes)
                    self.job.progress(
                        min(0.99, done / max(1, len(candidates))),
                        f"read {done}/{len(candidates)} papers",
                    )

        await asyncio.gather(*[read_one(p) for p in candidates])
        if failures:
            log.warning("reader failed on %s paper(s)", len(failures))
        return AgentResult(
            agent=self.name,
            output={"notes": len(board.paper_notes), "failed": len(failures)},
            text=f"read {len(board.paper_notes)} papers"
                 + (f", {len(failures)} failed" if failures else ""),
            tokens_in=total_in, tokens_out=total_out, model=model_used,
            warnings=[f"{len(failures)} paper(s) could not be read"] if failures else [],
        )


class SynthesiserAgent(Agent):
    name = "synthesiser"
    title = "Synthesise the literature"
    title_zh = "综合文献"
    description = "Groups papers into themes with consensus and disagreement."

    async def run(self, board: Blackboard) -> AgentResult:
        papers = board.top_papers(self.config.max_papers_in_context)
        keys = _keys_for(board, papers)
        prompt = "\n\n".join(filter(None, [
            prompts.format_project_context(board.project),
            prompts.format_analysis_context(board.analysis),
            f"LITERATURE ({len(papers)} papers):\n"
            + prompts.format_paper_list(papers, keys, notes=board.paper_notes),
        ]))
        payload, tin, tout, model = await self.ask(
            prompt, system=prompts.SYNTHESISER, json_mode=True, max_tokens=3500
        )
        themes = payload.get("themes") if isinstance(payload, dict) else None
        board.themes = themes if isinstance(themes, list) else []
        if isinstance(payload, dict):
            board.extra["synthesis"] = payload
        return AgentResult(
            agent=self.name, output=payload,
            text=f"{len(board.themes)} themes: "
                 + ", ".join(str(t.get('name')) for t in board.themes[:8]),
            tokens_in=tin, tokens_out=tout, model=model,
        )


class IdeatorAgent(Agent):
    name = "ideator"
    title = "Validate research gaps"
    title_zh = "验证研究缺口"
    description = "Filters computed gap candidates and positions the author's idea."

    async def run(self, board: Blackboard) -> AgentResult:
        papers = board.top_papers(min(self.config.max_papers_in_context, 30))
        keys = _keys_for(board, papers)
        analysis_context = prompts.format_analysis_context(board.analysis, max_gaps=10)
        if not analysis_context:
            analysis_context = (
                "NO COMPUTED LANDSCAPE AVAILABLE. Identify gaps from the "
                "literature itself and say that no quantitative gap analysis "
                "was available."
            )
        themes_text = ""
        if board.themes:
            themes_text = "IDENTIFIED THEMES:\n" + "\n".join(
                f"  - {t.get('name')} ({t.get('maturity', '?')}): "
                f"{truncate(str(t.get('description', '')), 200)}"
                for t in board.themes[:12]
            )
        prompt = "\n\n".join(filter(None, [
            prompts.format_project_context(board.project),
            analysis_context,
            themes_text,
            f"LITERATURE:\n" + prompts.format_paper_list(
                papers, keys, abstract_chars=220, notes=board.paper_notes
            ),
        ]))
        payload, tin, tout, model = await self.ask(
            prompt, system=prompts.IDEATOR, json_mode=True, max_tokens=3000
        )
        board.gap_analysis = payload if isinstance(payload, dict) else {}
        validated = board.gap_analysis.get("validated_gaps") or []
        rejected = board.gap_analysis.get("rejected_gaps") or []
        return AgentResult(
            agent=self.name, output=payload,
            text=f"{len(validated)} gaps validated, {len(rejected)} rejected",
            tokens_in=tin, tokens_out=tout, model=model,
        )


class OutlinerAgent(Agent):
    name = "outliner"
    title = "Build the section outline"
    title_zh = "构建章节大纲"
    description = "Turns the plan into per-section writing briefs with paper assignments."
    requires = ("plan",)

    async def run(self, board: Blackboard) -> AgentResult:
        papers = board.top_papers(self.config.max_papers_in_context)
        keys = _keys_for(board, papers)
        prompt = "\n\n".join(filter(None, [
            prompts.format_project_context(board.project),
            f"APPROVED PLAN:\n{_json_dump(board.plan)}",
            f"THEMES:\n{_json_dump(board.themes)}" if board.themes else "",
            f"GAP ANALYSIS:\n{_json_dump(board.gap_analysis)}"
            if board.gap_analysis else "",
            f"AVAILABLE PAPERS:\n" + prompts.format_paper_list(
                papers, keys, include_abstract=False, notes=None
            ),
            f"TOTAL BUDGET: {self.config.target_words} words.",
        ]))
        payload, tin, tout, model = await self.ask(
            prompt, system=prompts.OUTLINER, json_mode=True, max_tokens=3000
        )
        sections = payload.get("sections") if isinstance(payload, dict) else None
        board.outline = _normalise_outline(
            sections if isinstance(sections, list) else [], board, keys
        )
        return AgentResult(
            agent=self.name, output={"sections": board.outline},
            text=f"{len(board.outline)} sections: "
                 + ", ".join(s["key"] for s in board.outline),
            tokens_in=tin, tokens_out=tout, model=model,
        )


def _normalise_outline(
    raw_sections: list[dict[str, Any]], board: Blackboard, keys: dict[str, str]
) -> list[dict[str, Any]]:
    """Clean the model's outline into the shape the writer and store expect.

    Guards applied: keys are slugified and deduplicated (they become section keys
    and filenames), citation keys are mapped back to paper ids and unknown ones
    dropped, and word targets are clamped to something writable.
    """
    key_to_paper = {key: paper_id for paper_id, key in keys.items()}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, section in enumerate(raw_sections):
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        key = slugify(str(section.get("key") or title) or f"section-{index + 1}",
                      fallback=f"section-{index + 1}")
        while key in seen:
            key = f"{key}-2"
        seen.add(key)
        paper_ids = [
            key_to_paper[str(k).strip()]
            for k in (section.get("paper_keys") or [])
            if str(k).strip() in key_to_paper
        ]
        out.append({
            "key": key,
            "title": title or key.replace("-", " ").title(),
            "level": max(1, min(3, int(section.get("level") or 1))),
            "guidance": str(section.get("guidance") or section.get("purpose") or ""),
            "opening": str(section.get("opening") or ""),
            "must_not": str(section.get("must_not") or ""),
            "target_words": max(80, min(4000, int(section.get("target_words") or 600))),
            "paper_ids": paper_ids,
            "ordering": (index + 1) * 10,
        })
    return out


class WriterAgent(Agent):
    """Drafts one section. Streams so the editor fills as text arrives."""

    name = "writer"
    title = "Draft section"
    title_zh = "撰写章节"
    description = "Writes one section from its brief and assigned papers."
    requires = ("outline",)

    def __init__(self, *args: Any, section_key: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.section_key = section_key
        self.title = f"Draft: {section_key}" if section_key else "Draft section"

    async def run(self, board: Blackboard) -> AgentResult:
        section = next(
            (s for s in board.outline if s["key"] == self.section_key), None
        )
        if section is None:
            raise ValueError(
                f"section '{self.section_key}' is not in the outline "
                f"({[s['key'] for s in board.outline]})"
            )
        by_id = board.papers_by_id()
        keys = _keys_for(board, board.papers)
        assigned = [by_id[pid] for pid in section["paper_ids"] if pid in by_id]
        if not assigned:
            # An unassigned section still needs literature to cite; fall back to
            # the strongest papers rather than inviting the model to invent some.
            assigned = board.top_papers(12)

        already = {
            key: truncate(text, 500)
            for key, text in board.sections.items() if key != self.section_key
        }
        prompt = "\n\n".join(filter(None, [
            prompts.format_project_context(board.project),
            f"CONTRIBUTION CLAIM: {board.plan.get('contribution', '')}"
            if board.plan else "",
            f"SECTION TO WRITE: {section['title']} (key: {section['key']})\n"
            f"BRIEF: {section['guidance']}\n"
            + (f"OPENING SHOULD: {section['opening']}\n" if section['opening'] else "")
            + (f"DO NOT COVER HERE: {section['must_not']}\n"
               if section['must_not'] else "")
            + f"TARGET LENGTH: {section['target_words']} words",
            f"PAPERS YOU MAY CITE ({len(assigned)}):\n"
            + prompts.format_paper_list(
                assigned, keys, notes=board.paper_notes, abstract_chars=500
            ),
            f"VALIDATED GAPS (use for motivation):\n"
            f"{_json_dump(board.gap_analysis.get('validated_gaps'))}"
            if board.gap_analysis.get("validated_gaps") else "",
            f"THEMES:\n{_json_dump(board.themes)}" if board.themes
            and section["key"] in ("related-work", "related_work", "background")
            else "",
            "ALREADY DRAFTED SECTIONS (do not repeat them):\n"
            + "\n".join(f"[{k}] {v}" for k, v in already.items())
            if already else "",
            prompts._language_rule(self.config.language),
        ]))
        # Word budget -> token ceiling with headroom: ~1.6 tokens/word for
        # English prose, plus slack so the model is not cut off mid-sentence.
        max_tokens = min(8000, max(700, int(section["target_words"] * 2.2)))
        text, tin, tout, model = await self.ask_streaming(
            prompt, system=prompts.WRITER, section_key=section["key"],
            max_tokens=max_tokens,
        )
        cleaned = _strip_wrapper(text)
        board.sections[section["key"]] = cleaned
        board.modified_section_keys.add(section["key"])
        board.citations[section["key"]] = _extract_citation_keys(cleaned, keys)
        return AgentResult(
            agent=self.name, output={"section": section["key"],
                                     "words": word_count(cleaned)},
            text=cleaned, tokens_in=tin, tokens_out=tout, model=model,
        )


_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")
_LEADING_HEADING = re.compile(r"^\s*#{1,6}\s+.*?\n+")


def _strip_wrapper(text: str) -> str:
    """Remove artefacts models add despite being told not to.

    Code fences and a repeated section heading are the two persistent ones; the
    heading matters because the store adds its own, so leaving it produces a
    duplicated title in every export.
    """
    cleaned = _FENCE_RE.sub("", text.strip())
    cleaned = _LEADING_HEADING.sub("", cleaned, count=1)
    for preamble in (
        "Here is the section:", "Here's the section:", "Section:",
        "Here is the draft:", "Draft:",
    ):
        if cleaned.lstrip().startswith(preamble):
            cleaned = cleaned.lstrip()[len(preamble):].lstrip()
    return cleaned.strip()


_CITE_RE = re.compile(r"\[([A-Za-z][A-Za-z0-9]{2,20})\]")


def _extract_citation_keys(text: str, keys: dict[str, str]) -> list[str]:
    """Paper ids actually cited in the text, from its [KEY] markers."""
    reverse = {key: paper_id for paper_id, key in keys.items()}
    found: list[str] = []
    for match in _CITE_RE.finditer(text or ""):
        paper_id = reverse.get(match.group(1))
        if paper_id and paper_id not in found:
            found.append(paper_id)
    return found


class CriticAgent(Agent):
    name = "critic"
    title = "Review section"
    title_zh = "审阅章节"
    description = "Reviews a drafted section for unsupported claims and gaps."
    requires = ("sections",)

    def __init__(self, *args: Any, section_key: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.section_key = section_key
        self.title = f"Review: {section_key}" if section_key else "Review section"

    async def run(self, board: Blackboard) -> AgentResult:
        text = board.sections.get(self.section_key, "")
        if not text.strip():
            return AgentResult(
                agent=self.name, output={"verdict": "skipped"},
                text="section is empty", warnings=["nothing to review"],
            )
        section = next(
            (s for s in board.outline if s["key"] == self.section_key), {}
        )
        by_id = board.papers_by_id()
        keys = _keys_for(board, board.papers)
        allowed = [by_id[pid] for pid in section.get("paper_ids", []) if pid in by_id]
        prompt = "\n\n".join(filter(None, [
            f"SECTION: {section.get('title', self.section_key)}",
            f"BRIEF IT MUST SATISFY: {section.get('guidance', '(none recorded)')}",
            f"REQUIRED COVERAGE: {section.get('must_not', '')}",
            f"ALLOWED CITATION KEYS: "
            + ", ".join(keys[p.id] for p in (allowed or board.top_papers(20))),
            f"PAPER FACTS (for checking attributions):\n"
            + prompts.format_paper_list(
                allowed or board.top_papers(15), keys, notes=board.paper_notes,
                abstract_chars=380,
            ),
            f"DRAFTED TEXT:\n{text}",
        ]))
        payload, tin, tout, model = await self.ask(
            prompt, system=prompts.CRITIC, json_mode=True, max_tokens=2500
        )
        issues = payload.get("issues") if isinstance(payload, dict) else None
        board.critiques[self.section_key] = issues if isinstance(issues, list) else []
        verdict = (payload or {}).get("verdict", "unknown")
        high = sum(
            1 for i in board.critiques[self.section_key]
            if i.get("severity") == "high"
        )
        return AgentResult(
            agent=self.name, output=payload,
            text=f"{verdict}: {len(board.critiques[self.section_key])} issues "
                 f"({high} high severity)",
            tokens_in=tin, tokens_out=tout, model=model,
        )


class ReviserAgent(Agent):
    name = "reviser"
    title = "Revise section"
    title_zh = "修订章节"
    description = "Applies review issues to a section without gratuitous rewriting."
    requires = ("sections",)

    def __init__(self, *args: Any, section_key: str = "", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.section_key = section_key
        self.title = f"Revise: {section_key}" if section_key else "Revise section"

    async def run(self, board: Blackboard) -> AgentResult:
        text = board.sections.get(self.section_key, "")
        issues = board.critiques.get(self.section_key) or []
        actionable = [i for i in issues if i.get("severity") in ("high", "medium")]
        if not text.strip() or not actionable:
            return AgentResult(
                agent=self.name, output={"revised": False},
                text="no high/medium issues to fix",
            )
        section = next(
            (s for s in board.outline if s["key"] == self.section_key), {}
        )
        by_id = board.papers_by_id()
        keys = _keys_for(board, board.papers)
        allowed = [by_id[pid] for pid in section.get("paper_ids", []) if pid in by_id]
        prompt = "\n\n".join(filter(None, [
            f"SECTION: {section.get('title', self.section_key)}",
            f"BRIEF: {section.get('guidance', '')}",
            f"ISSUES TO FIX ({len(actionable)}):\n{_json_dump(actionable)}",
            f"PAPERS YOU MAY CITE:\n"
            + prompts.format_paper_list(
                allowed or board.top_papers(15), keys, notes=board.paper_notes,
                abstract_chars=380,
            ),
            f"CURRENT TEXT:\n{text}",
            prompts._language_rule(self.config.language),
        ]))
        revised, tin, tout, model = await self.ask_streaming(
            prompt, system=prompts.REVISER, section_key=self.section_key,
            max_tokens=min(8000, max(900, int(len(text) / 2))),
        )
        cleaned = _strip_wrapper(revised)
        if cleaned.strip():
            board.sections[self.section_key] = cleaned
            board.modified_section_keys.add(self.section_key)
            board.citations[self.section_key] = _extract_citation_keys(cleaned, keys)
        return AgentResult(
            agent=self.name,
            output={"revised": True, "issues_addressed": len(actionable),
                    "words": word_count(cleaned)},
            text=cleaned, tokens_in=tin, tokens_out=tout, model=model,
        )


class CitationAgent(Agent):
    name = "citation_checker"
    title = "Verify citations"
    title_zh = "核查引用"
    description = "Checks every [KEY] marker exists and supports its claim."
    requires = ("sections",)
    prefers_fast_model = True

    async def run(self, board: Blackboard) -> AgentResult:
        keys = _keys_for(board, board.papers)
        allowed_keys = set(keys.values())
        full_text = "\n\n".join(
            f"[[section:{k}]]\n{v}" for k, v in board.sections.items() if v.strip()
        )
        if not full_text.strip():
            return AgentResult(agent=self.name, output={}, text="nothing to check")

        # Structural check first, locally: unknown markers are found exactly,
        # without spending a model call or trusting the model to enumerate.
        used = set(_CITE_RE.findall(full_text))
        unknown = sorted(used - allowed_keys)
        unused = sorted(allowed_keys - used)

        prompt = "\n\n".join([
            f"ALLOWED KEYS: {', '.join(sorted(allowed_keys))}",
            f"PAPER FACTS:\n" + prompts.format_paper_list(
                board.top_papers(self.config.max_papers_in_context), keys,
                notes=board.paper_notes, abstract_chars=340,
            ),
            f"MANUSCRIPT:\n{truncate(full_text, 30000)}",
        ])
        payload, tin, tout, model = await self.ask(
            prompt, system=prompts.CITATION_AGENT, json_mode=True, max_tokens=2500
        )
        report = payload if isinstance(payload, dict) else {}
        # The locally computed facts override the model's - it miscounts.
        report["invalid"] = [
            {"key": key, "reason": "not in the allowed paper list"}
            for key in unknown
        ]
        report["unused_papers"] = unused[:40]
        report["markers_used"] = sorted(used)
        board.extra["citation_report"] = report
        return AgentResult(
            agent=self.name, output=report,
            text=f"{len(used)} markers, {len(unknown)} invalid, "
                 f"{len(report.get('questionable') or [])} questionable, "
                 f"{len(unused)} papers unused",
            tokens_in=tin, tokens_out=tout, model=model,
            warnings=[f"invalid citation keys: {', '.join(unknown[:10])}"]
            if unknown else [],
        )


class TranslatorAgent(Agent):
    name = "translator"
    title = "Translate section"
    title_zh = "翻译章节"
    description = "Produces the paired-language version of a section."
    requires = ("sections",)

    def __init__(
        self, *args: Any, section_key: str = "", target_language: str = "zh",
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.section_key = section_key
        self.target_language = target_language
        self.title = f"Translate: {section_key}" if section_key else "Translate"

    async def run(self, board: Blackboard) -> AgentResult:
        text = board.sections.get(self.section_key, "")
        if not text.strip():
            return AgentResult(agent=self.name, output={}, text="nothing to translate")
        language_name = {"zh": "Simplified Chinese (简体中文)",
                         "en": "English"}.get(self.target_language,
                                              self.target_language)
        prompt = (
            f"TARGET LANGUAGE: {language_name}\n\n"
            f"TEXT TO TRANSLATE:\n{text}"
        )
        translated, tin, tout, model = await self.ask_streaming(
            prompt, system=prompts.TRANSLATOR, section_key=self.section_key,
            max_tokens=min(8000, max(900, int(len(text) / 1.5))),
        )
        cleaned = _strip_wrapper(translated)
        board.translations[self.section_key] = cleaned
        board.modified_section_keys.add(self.section_key)
        return AgentResult(
            agent=self.name,
            output={"section": self.section_key, "language": self.target_language},
            text=cleaned, tokens_in=tin, tokens_out=tout, model=model,
        )


class PolisherAgent(Agent):
    """Final cross-section harmonisation.

    Exists because section-by-section drafting reliably produces inconsistent
    terminology and repeated explanations - problems no single-section agent can
    see. Returns targeted find/replace edits rather than rewritten sections, so
    the change is reviewable and cannot silently lose content.
    """

    name = "polisher"
    title = "Harmonise the manuscript"
    title_zh = "统一全文"
    description = "Fixes cross-section terminology, repetition and transitions."
    requires = ("sections",)

    async def run(self, board: Blackboard) -> AgentResult:
        if len(board.sections) < 2:
            return AgentResult(
                agent=self.name, output={}, text="fewer than 2 sections; nothing "
                                                 "to harmonise",
            )
        manuscript = "\n\n".join(
            f"[[section:{key}]]\n{text}"
            for key, text in board.sections.items() if text.strip()
        )
        prompt = "\n\n".join([
            prompts.format_project_context(board.project),
            f"MANUSCRIPT ({len(board.sections)} sections):\n"
            f"{truncate(manuscript, 40000)}",
        ])
        payload, tin, tout, model = await self.ask(
            prompt, system=prompts.POLISHER, json_mode=True, max_tokens=3000
        )
        edits = (payload or {}).get("edits") or []
        applied = 0
        skipped = 0
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            key = str(edit.get("section_key") or "")
            find = str(edit.get("find") or "")
            replace = str(edit.get("replace") or "")
            if not key or not find or key not in board.sections:
                skipped += 1
                continue
            current = board.sections[key]
            if find not in current:
                # The model paraphrased instead of quoting; applying a fuzzy
                # match risks corrupting the text, so the edit is dropped and
                # reported rather than guessed at.
                skipped += 1
                continue
            board.sections[key] = current.replace(find, replace, 1)
            board.modified_section_keys.add(key)
            applied += 1
        board.extra["polish_report"] = {
            **(payload if isinstance(payload, dict) else {}),
            "applied": applied, "skipped": skipped,
        }
        return AgentResult(
            agent=self.name, output=board.extra["polish_report"],
            text=f"applied {applied} of {len(edits)} edits ({skipped} could not be "
                 f"located verbatim)",
            tokens_in=tin, tokens_out=tout, model=model,
            warnings=[f"{skipped} edit(s) skipped: text not found verbatim"]
            if skipped else [],
        )


def _json_dump(value: Any) -> str:
    import json

    if value in (None, "", [], {}):
        return ""
    return json.dumps(value, ensure_ascii=False, indent=1)[:12000]


ALL_ROLES: dict[str, type[Agent]] = {
    "planner": PlannerAgent,
    "reader": ReaderAgent,
    "synthesiser": SynthesiserAgent,
    "ideator": IdeatorAgent,
    "outliner": OutlinerAgent,
    "writer": WriterAgent,
    "critic": CriticAgent,
    "reviser": ReviserAgent,
    "citation_checker": CitationAgent,
    "translator": TranslatorAgent,
    "polisher": PolisherAgent,
}


def describe_roles() -> list[dict[str, Any]]:
    """Role catalogue for the UI, built without instantiating anything."""
    return [
        {
            "name": cls.name,
            "title": cls.title,
            "title_zh": cls.title_zh,
            "description": cls.description,
            "requires": list(cls.requires),
            "prefers_fast_model": cls.prefers_fast_model,
            "per_section": cls in (WriterAgent, CriticAgent, ReviserAgent,
                                   TranslatorAgent),
        }
        for cls in ALL_ROLES.values()
    ]

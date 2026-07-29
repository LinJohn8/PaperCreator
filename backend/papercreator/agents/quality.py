"""Deterministic quality evidence for an Agent-produced manuscript.

An LLM saying that its own prose is well supported is not a quality gate.  This
module therefore keeps decidable checks (citation-key integrity, persisted
citation metadata, section targets and bilingual completeness) separate from
model-assisted judgements (questionable citations and uncited claims).  The
report is stored with the run and explicitly requires a human rubric before a
draft can be treated as accepted research output.
"""

from __future__ import annotations

from collections import Counter
import json
from typing import Any

from ..core.models import Paper
from ..core.util import sha256_text, utc_now_iso, word_count
from ..writing.citations import CitationKeyMap, MARKER
from .base import Blackboard

QUALITY_REPORT_SCHEMA_VERSION = 2
HUMAN_RUBRIC_SCHEMA_VERSION = 3
MANUSCRIPT_SNAPSHOT_SCHEMA_VERSION = 1


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_manuscript_snapshot(
    board: Blackboard,
    *,
    source_snapshot_id: str = "",
) -> dict[str, Any]:
    """Freeze the exact prose visible after an Agent run.

    Hashes alone are insufficient audit evidence: once the live manuscript is
    edited, a reviewer could no longer reconstruct the text represented by the
    hash.  The run therefore owns a compact immutable copy of every section,
    while ``source_snapshot_id`` links it to the ordinary version timeline.
    """

    outline_by_key = {
        str(item.get("key") or ""): item
        for item in board.outline
        if str(item.get("key") or "")
    }
    ordered_keys = [key for key in outline_by_key if key in board.sections]
    ordered_keys.extend(sorted(set(board.sections).difference(ordered_keys)))
    sections: list[dict[str, Any]] = []
    for key in ordered_keys:
        primary = str(board.sections.get(key) or "")
        paired = str(board.translations.get(key) or "")
        outline = outline_by_key.get(key) or {}
        sections.append(
            {
                "section_key": key,
                "title": str(outline.get("title") or key),
                "primary_text": primary,
                "primary_text_sha256": sha256_text(primary),
                "primary_text_chars": len(primary),
                "paired_text": paired,
                "paired_text_sha256": sha256_text(paired),
                "paired_text_chars": len(paired),
                "modified_by_run": key in board.modified_section_keys,
            }
        )
    content_evidence = {
        "schema_version": MANUSCRIPT_SNAPSHOT_SCHEMA_VERSION,
        "sections": sections,
    }
    return {
        **content_evidence,
        "source_snapshot_id": str(source_snapshot_id or ""),
        # Content addressing must not change merely because the identical prose
        # was captured under a different version-timeline row.
        "manuscript_fingerprint": sha256_text(_canonical_json(content_evidence)),
    }


def verify_manuscript_snapshot(
    manuscript_snapshot: dict[str, Any] | None,
    quality_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recompute every prose hash and report any missing or altered evidence."""

    snapshot = manuscript_snapshot if isinstance(manuscript_snapshot, dict) else {}
    problems: list[str] = []
    if int(snapshot.get("schema_version") or 0) != MANUSCRIPT_SNAPSHOT_SCHEMA_VERSION:
        problems.append("unsupported_manuscript_snapshot_schema")
    sections = snapshot.get("sections")
    sections = sections if isinstance(sections, list) else []
    if not sections:
        problems.append("manuscript_sections_missing")
    by_key: dict[str, dict[str, Any]] = {}
    for item in sections:
        if not isinstance(item, dict):
            problems.append("invalid_manuscript_section")
            continue
        key = str(item.get("section_key") or "")
        if not key or key in by_key:
            problems.append("invalid_or_duplicate_manuscript_section_key")
            continue
        by_key[key] = item
        for language in ("primary", "paired"):
            text = item.get(f"{language}_text")
            if not isinstance(text, str):
                problems.append(f"{key}:{language}_text_missing")
                continue
            if item.get(f"{language}_text_sha256") != sha256_text(text):
                problems.append(f"{key}:{language}_text_hash_mismatch")
            if item.get(f"{language}_text_chars") != len(text):
                problems.append(f"{key}:{language}_text_length_mismatch")

    content_evidence = {
        "schema_version": snapshot.get("schema_version"),
        "sections": sections,
    }
    computed_fingerprint = sha256_text(_canonical_json(content_evidence))
    if snapshot.get("manuscript_fingerprint") != computed_fingerprint:
        problems.append("manuscript_fingerprint_mismatch")

    report = quality_report if isinstance(quality_report, dict) else {}
    report_sections = report.get("sections")
    report_sections = report_sections if isinstance(report_sections, list) else []
    for evidence in report_sections:
        if not isinstance(evidence, dict):
            continue
        key = str(evidence.get("section_key") or "")
        frozen = by_key.get(key)
        if frozen is None:
            problems.append(f"{key}:quality_section_missing_from_manuscript")
            continue
        for field in (
            "primary_text_sha256",
            "primary_text_chars",
            "paired_text_sha256",
            "paired_text_chars",
        ):
            if evidence.get(field) != frozen.get(field):
                problems.append(f"{key}:quality_{field}_mismatch")
    return {
        "status": "pass" if not problems else "fail",
        "problems": sorted(set(problems)),
        "computed_fingerprint": computed_fingerprint,
        "section_count": len(by_key),
    }


def build_review_target(
    quality_report: dict[str, Any],
    *,
    run_status: str,
    manuscript_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze the exact automatic evidence a human rubric refers to.

    ``acceptance`` is intentionally excluded: appending a human review updates
    that projection, but must not change the fingerprint of the manuscript and
    automatic evidence that were reviewed.
    """

    sections = quality_report.get("sections")
    sections = sections if isinstance(sections, list) else []
    required_section_keys = sorted(
        {
            str(section.get("section_key") or "")
            for section in sections
            if isinstance(section, dict)
            and section.get("modified_by_run")
            and str(section.get("section_key") or "")
        }
    )
    registry = quality_report.get("citation_registry")
    registry = registry if isinstance(registry, dict) else {}
    required_paper_ids = sorted(
        {
            str(paper_id)
            for paper_id in registry.get("cited_paper_ids") or []
            if str(paper_id)
        }
    )
    immutable_evidence = {
        key: quality_report.get(key)
        for key in (
            "schema_version",
            "generated_at",
            "run_status",
            "gate",
            "summary",
            "metrics",
            "checks",
            "sections",
            "citation_registry",
            "limitations",
        )
    }
    report_schema = int(quality_report.get("schema_version") or 0)
    integrity = verify_manuscript_snapshot(manuscript_snapshot, quality_report)
    bound = report_schema >= 2 and integrity["status"] == "pass"
    target = {
        "rubric_version": HUMAN_RUBRIC_SCHEMA_VERSION if bound else 2,
        "run_status": run_status,
        "automatic_gate": str(quality_report.get("gate") or "unavailable"),
        "quality_report_schema_version": int(
            quality_report.get("schema_version") or 0
        ),
        "quality_report_generated_at": str(
            quality_report.get("generated_at") or ""
        ),
        "quality_report_fingerprint": sha256_text(_canonical_json(immutable_evidence)),
        "required_section_keys": required_section_keys,
        "required_paper_ids": required_paper_ids,
        "manuscript_integrity": integrity["status"] if report_schema >= 2 else "legacy_unbound",
        "manuscript_integrity_problems": integrity["problems"] if report_schema >= 2 else [],
    }
    if bound and isinstance(manuscript_snapshot, dict):
        target.update(
            {
                "manuscript_snapshot_schema_version": int(
                    manuscript_snapshot.get("schema_version") or 0
                ),
                "manuscript_source_snapshot_id": str(
                    manuscript_snapshot.get("source_snapshot_id") or ""
                ),
                "manuscript_fingerprint": str(
                    manuscript_snapshot.get("manuscript_fingerprint") or ""
                ),
                "manuscript_section_count": integrity["section_count"],
            }
        )
    return target


def _check(
    check_id: str,
    status: str,
    message: str,
    *,
    method: str = "deterministic",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "message": message,
        "method": method,
        "evidence": evidence or {},
    }


def build_quality_report(
    board: Blackboard,
    *,
    citation_papers: list[Paper] | None = None,
    expect_translation: bool = False,
    run_status: str = "done",
) -> dict[str, Any]:
    """Evaluate the manuscript state left by one Agent run without another call.

    ``citation_papers`` should contain the complete project citation registry,
    not only the subset selected for this run.  That makes author-year keys
    stable across full-paper, section and retry runs.
    """

    papers = citation_papers if citation_papers is not None else board.papers
    papers_by_id = {paper.id: paper for paper in papers}
    canonical = CitationKeyMap.build(papers).by_paper
    cached = board.extra.get("citation_keys")
    key_by_paper = (
        {str(k): str(v) for k, v in cached.items() if str(k) and str(v)}
        if isinstance(cached, dict)
        else {}
    )
    # The cached registry is what the model actually saw.  Fill only missing
    # entries from the shared canonical implementation; never silently remap a
    # marker after generation.
    for paper_id, key in canonical.items():
        key_by_paper.setdefault(paper_id, key)
    paper_by_key = {key: paper_id for paper_id, key in key_by_paper.items()}

    outline_by_key = {
        str(item.get("key") or ""): item
        for item in board.outline
        if str(item.get("key") or "")
    }
    ordered_keys = [
        key for key in outline_by_key if key in board.sections
    ] + sorted(set(board.sections).difference(outline_by_key))

    section_reports: list[dict[str, Any]] = []
    invalid_citations: list[dict[str, Any]] = []
    metadata_mismatches: list[dict[str, Any]] = []
    all_occurrences: list[str] = []
    all_cited_ids: list[str] = []
    total_words = 0
    sections_with_citations = 0
    missing_translations: list[str] = []
    short_modified_sections: list[dict[str, Any]] = []
    high_issues = 0
    medium_issues = 0

    for key in ordered_keys:
        text = board.sections.get(key, "")
        if not text.strip():
            continue
        occurrences = MARKER.findall(text)
        all_occurrences.extend(occurrences)
        unique_markers = list(dict.fromkeys(occurrences))
        unknown = [marker for marker in unique_markers if marker not in paper_by_key]
        resolved_ids = list(
            dict.fromkeys(
                paper_by_key[marker]
                for marker in unique_markers
                if marker in paper_by_key
            )
        )
        all_cited_ids.extend(resolved_ids)
        if occurrences:
            sections_with_citations += 1
        for marker in unknown:
            invalid_citations.append(
                {
                    "section_key": key,
                    "key": marker,
                    "occurrences": occurrences.count(marker),
                }
            )

        recorded_ids = list(dict.fromkeys(board.citations.get(key, [])))
        missing_in_metadata = sorted(set(resolved_ids).difference(recorded_ids))
        stale_in_metadata = sorted(set(recorded_ids).difference(resolved_ids))
        if missing_in_metadata or stale_in_metadata:
            metadata_mismatches.append(
                {
                    "section_key": key,
                    "missing_in_metadata": missing_in_metadata,
                    "stale_in_metadata": stale_in_metadata,
                }
            )

        words = word_count(text)
        total_words += words
        target = int(outline_by_key.get(key, {}).get("target_words") or 0)
        target_ratio = round(words / target, 3) if target else None
        if (
            key in board.modified_section_keys
            and target
            and target_ratio is not None
            and target_ratio < 0.35
        ):
            short_modified_sections.append(
                {"section_key": key, "words": words, "target_words": target}
            )

        issues = [item for item in board.critiques.get(key, []) if isinstance(item, dict)]
        severities = Counter(str(item.get("severity") or "") for item in issues)
        high_issues += severities["high"]
        medium_issues += severities["medium"]
        has_translation = bool(board.translations.get(key, "").strip())
        paired_text = str(board.translations.get(key) or "")
        if expect_translation and key in board.modified_section_keys and not has_translation:
            missing_translations.append(key)

        section_reports.append(
            {
                "section_key": key,
                "words": words,
                "target_words": target,
                "target_ratio": target_ratio,
                "modified_by_run": key in board.modified_section_keys,
                "citation_occurrences": len(occurrences),
                "citation_keys": unique_markers,
                "invalid_keys": unknown,
                "cited_paper_ids": resolved_ids,
                "recorded_cited_paper_ids": recorded_ids,
                "critic_issues": dict(severities),
                "has_translation": has_translation,
                "primary_text_sha256": sha256_text(text),
                "primary_text_chars": len(text),
                "paired_text_sha256": sha256_text(paired_text),
                "paired_text_chars": len(paired_text),
            }
        )

    cited_ids = list(dict.fromkeys(all_cited_ids))
    cited_without_abstract = [
        paper_id
        for paper_id in cited_ids
        if paper_id in papers_by_id and not papers_by_id[paper_id].abstract.strip()
    ]
    distinct_markers = list(dict.fromkeys(all_occurrences))
    model_report = board.extra.get("citation_report")
    model_report = model_report if isinstance(model_report, dict) else {}
    questionable = model_report.get("questionable") or []
    uncited_claims = model_report.get("uncited_claims") or []

    checks: list[dict[str, Any]] = []
    invalid_labels = [
        f"{item['key']} ({item['section_key']})" for item in invalid_citations
    ]
    checks.append(
        _check(
            "citation_key_integrity",
            "fail" if invalid_citations else "pass",
            (
                f"{len(invalid_citations)} invalid citation key(s): "
                + ", ".join(invalid_labels[:10])
                if invalid_citations
                else f"all {len(all_occurrences)} citation marker occurrence(s) resolve"
            ),
            evidence={"invalid": invalid_citations},
        )
    )
    checks.append(
        _check(
            "citation_metadata_sync",
            "fail" if metadata_mismatches else "pass",
            (
                f"{len(metadata_mismatches)} section(s) disagree with persisted citation metadata"
                if metadata_mismatches
                else "section citation metadata matches the final primary-language text"
            ),
            evidence={"mismatches": metadata_mismatches},
        )
    )
    if not key_by_paper or (total_words < 120 and not cited_ids):
        checks.append(
            _check(
                "literature_usage",
                "not_applicable",
                "too little prose or no project literature for a meaningful usage check",
            )
        )
    else:
        checks.append(
            _check(
                "literature_usage",
                "warn" if not cited_ids else "pass",
                (
                    "the manuscript uses no project literature"
                    if not cited_ids
                    else f"{len(cited_ids)} distinct project paper(s) are cited"
                ),
                evidence={"cited_paper_ids": cited_ids},
            )
        )
    checks.append(
        _check(
            "source_evidence_availability",
            "warn" if cited_without_abstract else "pass",
            (
                f"{len(cited_without_abstract)} cited paper(s) lack an abstract for support review"
                if cited_without_abstract
                else "all cited project papers have abstract evidence available"
            ),
            evidence={"paper_ids_without_abstract": cited_without_abstract},
        )
    )
    if model_report:
        semantic_status = "warn" if questionable or uncited_claims else "pass"
        checks.append(
            _check(
                "semantic_citation_review",
                semantic_status,
                (
                    f"model-assisted review flagged {len(questionable)} questionable citation(s) "
                    f"and {len(uncited_claims)} uncited claim(s)"
                ),
                method="model_assisted",
                evidence={
                    "questionable": questionable,
                    "uncited_claims": uncited_claims,
                    "report": model_report,
                },
            )
        )
    else:
        checks.append(
            _check(
                "semantic_citation_review",
                "not_run",
                "this pipeline did not run the model-assisted citation checker",
                method="model_assisted",
            )
        )
    critique_count = sum(len(items) for items in board.critiques.values())
    checks.append(
        _check(
            "critic_review",
            "not_run" if critique_count == 0 else (
                "warn" if high_issues or medium_issues else "pass"
            ),
            (
                "no critic evidence was produced by this pipeline"
                if critique_count == 0
                else f"critic recorded {high_issues} high and {medium_issues} medium issue(s)"
            ),
            method="model_assisted",
            evidence={
                "issue_count": critique_count,
                "high": high_issues,
                "medium": medium_issues,
            },
        )
    )
    checks.append(
        _check(
            "section_target_coverage",
            "warn" if short_modified_sections else "pass",
            (
                f"{len(short_modified_sections)} modified section(s) are below 35% of target"
                if short_modified_sections
                else "modified sections satisfy the minimum target-length signal"
            ),
            evidence={"short_sections": short_modified_sections},
        )
    )
    checks.append(
        _check(
            "paired_translation",
            "not_applicable" if not expect_translation else (
                "warn" if missing_translations else "pass"
            ),
            (
                "translation was not requested"
                if not expect_translation
                else (
                    f"missing paired text for {len(missing_translations)} modified section(s)"
                    if missing_translations
                    else "all modified sections have paired translated text"
                )
            ),
            evidence={"missing_section_keys": missing_translations},
        )
    )

    statuses = {item["status"] for item in checks}
    gate = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
    return {
        "schema_version": QUALITY_REPORT_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "run_status": run_status,
        "gate": gate,
        "summary": {
            "pass": sum(item["status"] == "pass" for item in checks),
            "warn": sum(item["status"] == "warn" for item in checks),
            "fail": sum(item["status"] == "fail" for item in checks),
            "not_run": sum(
                item["status"] in {"not_run", "not_applicable"} for item in checks
            ),
        },
        "metrics": {
            "sections_evaluated": len(section_reports),
            "modified_sections": len(board.modified_section_keys),
            "words": total_words,
            "citation_marker_occurrences": len(all_occurrences),
            "distinct_citation_keys": len(distinct_markers),
            "invalid_citation_keys": len(invalid_citations),
            "cited_papers": len(cited_ids),
            "sections_with_citations": sections_with_citations,
            "citation_density_per_1000_words": round(
                len(all_occurrences) * 1000 / total_words, 2
            ) if total_words else 0.0,
            "questionable_citations": len(questionable),
            "uncited_claims": len(uncited_claims),
            "high_critic_issues": high_issues,
            "medium_critic_issues": medium_issues,
        },
        "checks": checks,
        "sections": section_reports,
        "citation_registry": {
            "paper_count": len(key_by_paper),
            "keys_used": distinct_markers,
            "cited_paper_ids": cited_ids,
            "papers": [
                {
                    "paper_id": paper_id,
                    "key": key_by_paper.get(paper_id, ""),
                    "title": papers_by_id[paper_id].title,
                    "year": papers_by_id[paper_id].year,
                    "doi": papers_by_id[paper_id].doi,
                    "url": papers_by_id[paper_id].url,
                    "pdf_path": papers_by_id[paper_id].pdf_path,
                    "abstract": papers_by_id[paper_id].abstract,
                    "abstract_sha256": sha256_text(
                        papers_by_id[paper_id].abstract
                    ),
                    "abstract_available": bool(
                        papers_by_id[paper_id].abstract.strip()
                    ),
                }
                for paper_id in cited_ids
                if paper_id in papers_by_id
            ],
        },
        "review_requirements": {
            "rubric_version": HUMAN_RUBRIC_SCHEMA_VERSION,
            "accepted_run_statuses": ["done"],
            "accepted_automatic_gates": ["pass", "warn"],
            "minimum_dimension_score": 3,
            "reviewer_required": True,
            "evidence_notes_required": True,
            "warning_acknowledgement_required": gate == "warn",
            "immutable_manuscript_required": True,
            "required_section_keys": sorted(board.modified_section_keys),
            "required_paper_ids": cited_ids,
        },
        "acceptance": {
            "automatic_gate": gate,
            "human_review_required": True,
            "semantic_grounding_verified": False,
            "latest_human_decision": "unreviewed",
        },
        "limitations": [
            "Structural checks prove key and metadata integrity, not that a claim is true.",
            "Model-assisted critic and citation judgements are evidence, not a gold label.",
            "The frozen manuscript proves which prose was reviewed, not that the prose is true.",
            "A human must inspect the cited source and record the rubric before acceptance.",
        ],
    }

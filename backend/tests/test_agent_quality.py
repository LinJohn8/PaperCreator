"""Deterministic Agent quality and citation-registry contracts."""

from __future__ import annotations

from papercreator.agents import prompts, quality
from papercreator.agents.base import Blackboard, RunConfig
from papercreator.agents.orchestrator import Orchestrator, load_blackboard
from papercreator.core.models import Author, Paper
from papercreator.store import documents as documents_store
from papercreator.store import papers as papers_store
from papercreator.writing import citations, manuscript


def _same_author_papers() -> list[Paper]:
    return [
        Paper(
            title="First collision paper",
            abstract="The first paper reports a controlled result.",
            authors=[Author(name="Alex Smith")],
            year=2024,
            doi="10.9999/quality.first",
        ).ensure_id(),
        Paper(
            title="Second collision paper",
            abstract="The second paper reports a distinct controlled result.",
            authors=[Author(name="Alex Smith")],
            year=2024,
            doi="10.9999/quality.second",
        ).ensure_id(),
    ]


def test_prompt_and_export_use_one_collision_rule(project):
    papers = _same_author_papers()
    prompt_keys = prompts.build_citation_keys(papers)
    export_keys = citations.CitationKeyMap.build(papers).by_paper

    assert prompt_keys == export_keys
    assert list(prompt_keys.values()) == ["SMITH2024", "SMITH2024a"]

    board = Blackboard(
        project=project,
        papers=papers,
        sections={
            "related-work": (
                "Prior controlled studies established both observations "
                "[SMITH2024][SMITH2024a]."
            )
        },
        citations={"related-work": [papers[0].id, papers[1].id]},
        translations={"related-work": "已有研究报告了这两项发现。"},
        modified_section_keys={"related-work"},
        outline=[
            {
                "key": "related-work",
                "title": "Related work",
                "target_words": 12,
            }
        ],
        extra={"citation_keys": prompt_keys},
    )
    report = quality.build_quality_report(
        board, citation_papers=papers, expect_translation=True
    )

    assert report["gate"] == "pass"
    assert report["metrics"]["invalid_citation_keys"] == 0
    assert report["metrics"]["cited_papers"] == 2
    assert [item["key"] for item in report["citation_registry"]["papers"]] == [
        "SMITH2024", "SMITH2024a"
    ]
    assert report["schema_version"] == 2
    assert report["review_requirements"]["rubric_version"] == 3
    assert report["review_requirements"]["required_section_keys"] == [
        "related-work"
    ]
    assert report["review_requirements"]["required_paper_ids"] == [
        papers[0].id, papers[1].id
    ]
    assert report["acceptance"]["human_review_required"] is True
    assert report["acceptance"]["semantic_grounding_verified"] is False
    snapshot = quality.build_manuscript_snapshot(
        board, source_snapshot_id="snp_quality_after"
    )
    target = quality.build_review_target(
        report, run_status="done", manuscript_snapshot=snapshot
    )
    assert target["rubric_version"] == 3
    assert target["automatic_gate"] == "pass"
    assert target["required_section_keys"] == ["related-work"]
    assert target["required_paper_ids"] == sorted([papers[0].id, papers[1].id])
    assert len(target["quality_report_fingerprint"]) == 64
    assert target["manuscript_fingerprint"] == snapshot["manuscript_fingerprint"]
    assert target["manuscript_integrity"] == "pass"
    section = report["sections"][0]
    assert section["primary_text_sha256"] == snapshot["sections"][0][
        "primary_text_sha256"
    ]
    assert section["paired_text_sha256"] == snapshot["sections"][0][
        "paired_text_sha256"
    ]
    report["acceptance"]["latest_human_decision"] = "accepted"
    assert quality.build_review_target(
        report, run_status="done", manuscript_snapshot=snapshot
    ) == target


def test_manuscript_snapshot_is_stable_and_tampering_is_detected(project):
    board = Blackboard(
        project=project,
        sections={"results": "Exact primary prose."},
        translations={"results": "精确的对照正文。"},
        modified_section_keys={"results"},
        outline=[{"key": "results", "title": "Results"}],
    )
    report = quality.build_quality_report(board)
    first = quality.build_manuscript_snapshot(board, source_snapshot_id="snp_after")
    second = quality.build_manuscript_snapshot(board, source_snapshot_id="snp_after")
    assert first == second
    relocated = quality.build_manuscript_snapshot(board, source_snapshot_id="snp_other")
    assert relocated["manuscript_fingerprint"] == first["manuscript_fingerprint"]
    assert quality.verify_manuscript_snapshot(first, report)["status"] == "pass"

    changed_board = Blackboard(
        project=project,
        sections={"results": "Exact primary prose, later edited."},
        translations={"results": "精确的对照正文。"},
        modified_section_keys={"results"},
        outline=board.outline,
    )
    changed = quality.build_manuscript_snapshot(
        changed_board, source_snapshot_id="snp_after"
    )
    assert changed["manuscript_fingerprint"] != first["manuscript_fingerprint"]

    tampered = {**first, "sections": [dict(first["sections"][0])]}
    tampered["sections"][0]["primary_text"] = "Altered without updating hashes."
    verification = quality.verify_manuscript_snapshot(tampered, report)
    assert verification["status"] == "fail"
    assert "results:primary_text_hash_mismatch" in verification["problems"]


def test_legacy_quality_report_remains_v2_but_is_not_manuscript_bound():
    legacy = {
        "schema_version": 1,
        "generated_at": "2026-07-28T00:00:00Z",
        "gate": "pass",
        "sections": [{"section_key": "intro", "modified_by_run": True}],
        "citation_registry": {"cited_paper_ids": []},
    }
    target = quality.build_review_target(legacy, run_status="done")
    assert target["rubric_version"] == 2
    assert target["manuscript_integrity"] == "legacy_unbound"
    assert "manuscript_fingerprint" not in target


def test_quality_report_separates_structural_failure_from_model_warning(project):
    paper = _same_author_papers()[0]
    key = prompts.build_citation_keys([paper])[paper.id]
    board = Blackboard(
        project=project,
        papers=[paper],
        sections={"introduction": f"A supported statement [{key}] and a bad one [FAKE2024]."},
        citations={"introduction": [paper.id]},
        modified_section_keys={"introduction"},
        outline=[{"key": "introduction", "target_words": 10}],
        extra={
            "citation_keys": {paper.id: key},
            "citation_report": {
                "questionable": [{"key": key, "claim": "A supported statement"}],
                "uncited_claims": ["a separate empirical claim"],
            },
        },
    )

    report = quality.build_quality_report(board, citation_papers=[paper])
    checks = {item["id"]: item for item in report["checks"]}
    assert report["gate"] == "fail"
    assert checks["citation_key_integrity"]["status"] == "fail"
    assert checks["semantic_citation_review"]["status"] == "warn"
    assert checks["semantic_citation_review"]["method"] == "model_assisted"


def test_selected_subset_keeps_project_level_key_and_persist_reindexes_final_text(project):
    papers = [papers_store.upsert(paper) for paper in _same_author_papers()]
    collection = papers_store.ensure_collection(project.id, "quality sources")
    papers_store.add_to_collection(collection["id"], [paper.id for paper in papers])
    try:
        board = load_blackboard(project.id, paper_ids=[papers[1].id])
        canonical = citations.CitationKeyMap.build(papers).by_paper
        assert board.extra["citation_keys"][papers[1].id] == canonical[papers[1].id]
        assert canonical[papers[1].id] == "SMITH2024a"

        manuscript.apply_template(project.id, "generic")
        document = documents_store.primary_document(project.id)
        persisted_board = Blackboard(
            project=project,
            papers=[papers[1]],
            sections={
                "introduction": f"Final polished claim [{canonical[papers[1].id]}]."
            },
            # Deliberately stale: persistence must trust final text instead.
            citations={"introduction": [papers[0].id]},
            modified_section_keys={"introduction"},
            outline=[{"key": "introduction", "title": "Introduction"}],
            extra={
                "document_id": document.id,
                "citation_keys": canonical,
                "citation_paper_ids": [paper.id for paper in papers],
            },
        )
        orchestrator = Orchestrator(
            project_id=project.id,
            pipeline="section",
            config=RunConfig(),
            run_id="run_quality_persist",
        )
        assert orchestrator._persist(persisted_board)["sections"] == 1
        section = documents_store.get_section_by_key(document.id, "introduction")
        assert section is not None
        assert section.cited_paper_ids == [papers[1].id]
        assert persisted_board.citations["introduction"] == [papers[1].id]
    finally:
        papers_store.delete_many([paper.id for paper in papers])

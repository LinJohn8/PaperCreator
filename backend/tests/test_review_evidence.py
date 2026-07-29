"""Immutable manuscript, blind packet and inter-reviewer agreement contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from papercreator.agents import quality
from papercreator.agents.base import Blackboard
from papercreator.api.app import create_app
from papercreator.core.models import Paper
from papercreator.store import projects as projects_store
from papercreator.store import runs as runs_store


@pytest.fixture()
def client(temp_home: Path):
    with TestClient(create_app()) as test_client:
        yield test_client


def _finished_evidence_run(project, *, pipeline: str = "full_auto") -> tuple[str, dict]:
    paper = Paper(
        id="pap_frozen_source",
        title="Frozen source evidence",
        abstract="The archived abstract supports the cited proposition.",
        doi="10.9999/frozen.source",
        year=2026,
    )
    board = Blackboard(
        project=project,
        papers=[paper],
        sections={"results": "The archived proposition is supported [FROZEN2026]."},
        translations={"results": "该归档命题有来源支持。"},
        citations={"results": [paper.id]},
        modified_section_keys={"results"},
        outline=[{"key": "results", "title": "Results", "target_words": 8}],
        extra={"citation_keys": {paper.id: "FROZEN2026"}},
    )
    report = quality.build_quality_report(
        board, citation_papers=[paper], expect_translation=True
    )
    manuscript = quality.build_manuscript_snapshot(
        board, source_snapshot_id="snp_frozen_after"
    )
    run_id = runs_store.create_run(
        project_id=project.id,
        pipeline=pipeline,
        request={"model": "provider/secret-model-name", "token_budget": 1234},
    )
    step_id = runs_store.create_step(
        run_id,
        agent="writer",
        ordering=1,
        model="provider/secret-model-name",
        prompt="private prompt that must not enter a packet",
    )
    runs_store.finish_step(step_id, output="private raw model output")
    runs_store.finish_run(
        run_id,
        result={"quality_report": report, "review_manuscript": manuscript},
    )
    return run_id, manuscript


def _evaluation(reviewer: str, decision: str, score: int) -> dict:
    return {
        "rubric_version": 3,
        "reviewer": reviewer,
        "decision": decision,
        "dimensions": {
            "factual_grounding": score,
            "citation_support": score,
            "methodological_soundness": score,
            "literature_coverage": score,
            "argument_coherence": score,
            "writing_clarity": score,
        },
        "source_evidence_checked": True,
        "reviewed_section_keys": ["results"],
        "reviewed_paper_ids": ["pap_frozen_source"],
        "notes": "Independent evidence note with enough detail.",
    }


def test_blind_packet_hides_identity_and_analysis_packet_restores_it(
    client: TestClient, project
):
    run_id, _ = _finished_evidence_run(project)
    runs_store.append_human_evaluation(
        run_id, _evaluation("Expert Alice", "accepted", 4)
    )

    blind_response = client.get(
        f"/api/agents/runs/{run_id}/review-packet", params={"kind": "blind"}
    )
    assert blind_response.status_code == 200, blind_response.text
    blind = blind_response.json()
    blind_text = json.dumps(blind, ensure_ascii=False)
    assert blind["identity_hidden"] is True
    assert blind["evidence_contract"]["manuscript_integrity"] == "pass"
    assert blind["manuscript"]["sections"][0]["primary_text"]
    assert blind["source_evidence"][0]["abstract"]
    assert "provenance" not in blind
    assert "human_evaluations" not in blind
    assert "source_snapshot_id" not in blind["manuscript"]
    assert "manuscript_source_snapshot_id" not in blind["evidence_contract"]
    assert "pdf_path" not in blind["source_evidence"][0]
    assert run_id not in blind_text
    assert project.id not in blind_text
    assert "provider/secret-model-name" not in blind_text
    assert "Expert Alice" not in blind_text
    assert "private prompt" not in blind_text
    assert "private raw model output" not in blind_text
    assert "pdf_path" not in blind_text
    assert "pdf_path" not in blind_text

    analysis_response = client.get(
        f"/api/agents/runs/{run_id}/review-packet", params={"kind": "analysis"}
    )
    assert analysis_response.status_code == 200, analysis_response.text
    analysis = analysis_response.json()
    assert analysis["identity_hidden"] is False
    assert analysis["provenance"]["run_id"] == run_id
    assert analysis["provenance"]["pipeline"] == "full_auto"
    assert analysis["provenance"]["models"] == ["provider/secret-model-name"]
    assert analysis["human_evaluations"][0]["reviewer"] == "Expert Alice"
    assert analysis["packet_fingerprint"] != blind["packet_fingerprint"]


def test_acceptance_rejects_stale_or_tampered_manuscript_evidence(
    client: TestClient, project
):
    run_id, manuscript = _finished_evidence_run(project, pipeline="section")
    run = runs_store.require_run(run_id)
    result = run["result"]
    tampered = {**result["review_manuscript"]}
    tampered["sections"] = [dict(item) for item in tampered["sections"]]
    tampered["sections"][0]["primary_text"] = "Tampered after the evidence was frozen."
    runs_store.finish_run(
        run_id,
        result={**result, "review_manuscript": tampered},
    )
    payload = {
        "reviewer": "Independent reviewer",
        "decision": "accepted",
        "factual_grounding": 4,
        "citation_support": 4,
        "methodological_soundness": 4,
        "literature_coverage": 4,
        "argument_coherence": 4,
        "writing_clarity": 4,
        "source_evidence_checked": True,
        "automatic_warnings_acknowledged": True,
        "reviewed_section_keys": ["results"],
        "reviewed_paper_ids": ["pap_frozen_source"],
        "reviewed_manuscript_fingerprint": manuscript["manuscript_fingerprint"],
        "review_mode": "blind",
        "notes": "Opened the source and checked the exact archived claim text.",
    }
    stale = client.post(f"/api/agents/runs/{run_id}/evaluations", json=payload)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "agent_evaluation_stale_manuscript"

    integrity = client.post(
        f"/api/agents/runs/{run_id}/evaluations",
        json={**payload, "reviewed_manuscript_fingerprint": ""},
    )
    assert integrity.status_code == 422
    blockers = integrity.json()["error"]["details"]["blockers"]
    assert "immutable_manuscript_integrity_failed" in blockers
    assert "reviewed_manuscript_fingerprint_mismatch" in blockers


def test_review_packet_export_stays_in_selected_workbench_project(
    client: TestClient, project
):
    run_id, _ = _finished_evidence_run(project, pipeline="section")
    response = client.post(
        f"/api/agents/runs/{run_id}/review-packet/export",
        json={"kind": "blind"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    target = Path(payload["path"]).resolve()
    expected_root = (projects_store.project_root(project) / "exports" / "reviews").resolve()
    assert target.is_relative_to(expected_root)
    assert target.is_file()
    exported = json.loads(target.read_text(encoding="utf-8"))
    assert exported["packet_kind"] == "blind"
    assert exported["packet_fingerprint"] == payload["packet_fingerprint"]


def test_acceptance_rejects_a_missing_stale_or_tampered_manuscript(
    client: TestClient, project
):
    run_id, manuscript = _finished_evidence_run(project, pipeline="section")
    result = runs_store.require_run(run_id)["result"]
    result["review_manuscript"]["sections"][0]["primary_text"] = (
        "Tampered after the fingerprint was issued."
    )
    runs_store.finish_run(run_id, result=result)
    request = {
        "reviewer": "Independent reviewer",
        "decision": "accepted",
        "factual_grounding": 4,
        "citation_support": 4,
        "methodological_soundness": 4,
        "literature_coverage": 4,
        "argument_coherence": 4,
        "writing_clarity": 4,
        "source_evidence_checked": True,
        "automatic_warnings_acknowledged": True,
        "reviewed_section_keys": ["results"],
        "reviewed_paper_ids": ["pap_frozen_source"],
        "reviewed_manuscript_fingerprint": manuscript["manuscript_fingerprint"],
        "notes": "Checked the exact frozen prose and its cited source evidence.",
    }
    stale = client.post(f"/api/agents/runs/{run_id}/evaluations", json=request)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "agent_evaluation_stale_manuscript"

    missing = client.post(
        f"/api/agents/runs/{run_id}/evaluations",
        json={**request, "reviewed_manuscript_fingerprint": ""},
    )
    assert missing.status_code == 422
    blockers = missing.json()["error"]["details"]["blockers"]
    assert "immutable_manuscript_integrity_failed" in blockers
    assert "reviewed_manuscript_fingerprint_mismatch" in blockers


def test_tampered_or_stale_manuscript_cannot_be_accepted(
    client: TestClient, project
):
    run_id, original = _finished_evidence_run(project, pipeline="section")
    run = runs_store.require_run(run_id)
    result = json.loads(json.dumps(run["result"]))
    result["review_manuscript"]["sections"][0]["primary_text"] = (
        "Tampered prose with the old hash."
    )
    runs_store.finish_run(run_id, result=result)
    payload = {
        "reviewer": "Independent reviewer",
        "decision": "accepted",
        "factual_grounding": 4,
        "citation_support": 4,
        "methodological_soundness": 4,
        "literature_coverage": 4,
        "argument_coherence": 4,
        "writing_clarity": 4,
        "source_evidence_checked": True,
        "automatic_warnings_acknowledged": True,
        "reviewed_section_keys": ["results"],
        "reviewed_paper_ids": ["pap_frozen_source"],
        "reviewed_manuscript_fingerprint": original["manuscript_fingerprint"],
        "notes": "I inspected the exact frozen prose and its cited source.",
    }
    stale = client.post(f"/api/agents/runs/{run_id}/evaluations", json=payload)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "agent_evaluation_stale_manuscript"

    invalid = client.post(
        f"/api/agents/runs/{run_id}/evaluations",
        json={**payload, "reviewed_manuscript_fingerprint": ""},
    )
    assert invalid.status_code == 422
    blockers = invalid.json()["error"]["details"]["blockers"]
    assert "immutable_manuscript_integrity_failed" in blockers
    assert "reviewed_manuscript_fingerprint_mismatch" in blockers


def test_agreement_statistics_use_distinct_identified_reviewers(project):
    first, _ = _finished_evidence_run(project, pipeline="section")
    second, _ = _finished_evidence_run(project, pipeline="stitch")
    runs_store.append_human_evaluation(first, _evaluation("Alice", "accepted", 4))
    runs_store.append_human_evaluation(first, _evaluation("Bob", "accepted", 5))
    runs_store.append_human_evaluation(second, _evaluation("Alice", "accepted", 4))
    runs_store.append_human_evaluation(second, _evaluation("Bob", "rejected", 1))
    # A repeated review by the same identity remains in history but must not
    # masquerade as an independent agreement pair.
    runs_store.append_human_evaluation(second, _evaluation("Alice", "accepted", 4))

    summary = runs_store.evaluation_summary(project.id)
    agreement = summary["agreement"]
    assert summary["schema_version"] == 2
    assert agreement["status"] == "available"
    assert agreement["reviewer_count"] == 2
    # first: Alice/Bob; second: two Alice records paired with Bob
    assert agreement["review_pair_count"] == 3
    assert agreement["decision_exact_agreement"] == pytest.approx(1 / 3, abs=0.0001)
    assert -1 <= agreement["decision_kappa"] <= 1
    assert agreement["scores"]["pair_count"] == 18
    assert agreement["scores"]["mean_absolute_difference"] == pytest.approx(
        7 / 3, abs=0.0001
    )
    assert agreement["scores"]["within_one_rate"] == pytest.approx(
        1 / 3, abs=0.0001
    )
    assert -1 <= agreement["scores"]["quadratic_weighted_kappa"] <= 1

"""API tests through FastAPI's TestClient.

These check the contract the frontend depends on: response shapes, the error
envelope, and that destructive operations refuse without explicit confirmation.
No network and no LLM is required - the endpoints that need them must fail with a
clear configuration error rather than hanging or crashing.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(temp_home):
    from papercreator.api.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


class TestSystemRoutes:
    def test_health_reports_every_subsystem(self, client):
        """The frontend uses this single call to decide what to enable."""
        response = client.get("/api/system/health")
        assert response.status_code == 200
        payload = response.json()
        for key in ("version", "paths", "database", "retrieval", "llm",
                    "analysis", "export", "git"):
            assert key in payload, f"health is missing {key}"
        assert isinstance(payload["retrieval"]["available"], list)
        assert isinstance(payload["llm"]["has_any"], bool)
        assert "embedding_backends" in payload["analysis"]

    def test_unavailable_providers_explain_themselves(self, client):
        payload = client.get("/api/system/health").json()
        for provider_id, reason in payload["retrieval"]["unavailable"].items():
            assert reason, f"{provider_id} is unavailable with no reason given"

    def test_capabilities_lists_the_ui_options(self, client):
        payload = client.get("/api/system/capabilities").json()
        for key in ("retrieval_providers", "analysis", "agent_pipelines",
                    "agent_roles", "templates", "export"):
            assert key in payload
        assert len(payload["agent_pipelines"]) == 4
        assert len(payload["agent_roles"]) >= 10

    def test_missing_job_returns_the_error_envelope(self, client):
        response = client.get("/api/system/jobs/does-not-exist")
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "not_found"
        assert body["error"]["message"]

    def test_logs_endpoint_rejects_an_unknown_file(self, client):
        assert client.get("/api/system/logs", params={"which": "secrets"}).status_code == 422

    def test_desktop_shutdown_is_hidden_without_its_capability(self, client):
        response = client.post(
            "/api/system/shutdown",
            headers={"X-PaperCreator-Shutdown": "not-a-real-capability"},
        )
        assert response.status_code == 404

    def test_desktop_shutdown_requires_exact_capability(self, client, monkeypatch):
        token = "e2e-private-desktop-capability"
        called: list[bool] = []
        monkeypatch.setenv("PC_DESKTOP_SHUTDOWN_TOKEN", token)
        client.app.state.request_shutdown = lambda: called.append(True)
        try:
            assert client.post(
                "/api/system/shutdown",
                headers={"X-PaperCreator-Shutdown": "wrong"},
            ).status_code == 404
            response = client.post(
                "/api/system/shutdown",
                headers={"X-PaperCreator-Shutdown": token},
            )
            assert response.status_code == 200
            assert response.json() == {"ok": True}
            assert called == [True]
        finally:
            del client.app.state.request_shutdown


class TestSettingsRoutes:
    def test_secrets_are_masked(self, client):
        payload = client.get("/api/settings").json()
        for value in payload["provider_keys"].values():
            assert value in ("", "***set***"), "a raw key must never be returned"

    def test_patch_round_trips_a_non_secret(self, client):
        response = client.patch(
            "/api/settings", json={"analysis": {"heatmap_grid": 32}}
        )
        assert response.status_code == 200
        assert response.json()["analysis"]["heatmap_grid"] == 32
        client.patch("/api/settings", json={"analysis": {"heatmap_grid": 40}})

    def test_quick_start_preference_round_trips(self, client):
        initial = client.get("/api/settings").json()
        assert initial["ui"]["quick_start_version"] == 0

        response = client.patch(
            "/api/settings", json={"ui": {"quick_start_version": 1}}
        )
        assert response.status_code == 200
        assert response.json()["ui"]["quick_start_version"] == 1
        assert client.get("/api/system/health").json()["ui"][
            "quick_start_version"
        ] == 1

        reset = client.patch(
            "/api/settings", json={"ui": {"quick_start_version": 0}}
        )
        assert reset.status_code == 200

    def test_patch_can_clear_a_non_secret_string(self, client):
        assert client.patch(
            "/api/settings", json={"identity": {"contact_email": "clear@example.edu"}}
        ).status_code == 200
        response = client.patch(
            "/api/settings", json={"identity": {"contact_email": ""}}
        )
        assert response.status_code == 200
        assert response.json()["identity"]["contact_email"] == ""

    def test_setting_sources_are_value_free(self, client):
        response = client.get("/api/settings/sources")
        assert response.status_code == 200
        payload = response.json()
        assert payload["precedence"][-1] == "environment"
        assert "fields" in payload["settings_file"]
        assert "fields" in payload["secrets_file"]
        assert "values" not in payload

    def test_empty_patch_is_rejected(self, client):
        response = client.patch("/api/settings", json={})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_disabling_every_provider_is_refused(self, client):
        """Leaving the user with no source would make search silently useless."""
        response = client.put("/api/settings/retrieval/enabled",
                              json={"provider_ids": []})
        assert response.status_code == 422
        assert "at least one" in response.json()["error"]["message"]

    def test_unknown_provider_is_rejected(self, client):
        response = client.put(
            "/api/settings/retrieval/enabled", json={"provider_ids": ["nope"]}
        )
        assert response.status_code == 422

    def test_analysis_backends_report_blockers(self, client):
        payload = client.get("/api/settings/analysis/backends").json()
        assert payload["embedding_backends"]
        assert "current" in payload


class TestProjectRoutes:
    def test_create_scaffolds_and_applies_a_template(self, client):
        response = client.post(
            "/api/projects",
            json={
                "title": "API Test Project",
                "idea": "Testing the HTTP surface.",
                "template_id": "short",
                "git_enabled": False,
                "apply_template": True,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        project_id = payload["project"]["id"]
        assert payload["project"]["slug"]
        assert payload["document"]["sections"], "template produced no sections"

        detail = client.get(f"/api/projects/{project_id}").json()
        assert detail["stats"]["sections"] > 0
        assert "bilingual" in detail
        assert "git" in detail

        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_title_is_required(self, client):
        assert client.post("/api/projects", json={"title": "  "}).status_code == 422

    def test_missing_project_is_a_clean_404(self, client):
        response = client.get("/api/projects/prj_nonexistent")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


class TestWritingRoutes:
    @pytest.fixture()
    def api_project(self, client):
        payload = client.post(
            "/api/projects",
            json={"title": "Writing Test", "template_id": "generic",
                  "git_enabled": False, "apply_template": True},
        ).json()
        yield payload["project"]["id"]
        client.delete(f"/api/projects/{payload['project']['id']}",
                      params={"remove_files": True})

    def test_section_edit_round_trip(self, client, api_project):
        response = client.patch(
            f"/api/writing/{api_project}/sections/introduction",
            json={"content": "The introduction body text."},
        )
        assert response.status_code == 200
        assert response.json()["section"]["word_count"] == 4

        fetched = client.get(f"/api/writing/{api_project}/sections/introduction").json()
        assert fetched["content"] == "The introduction body text."
        assert fetched["status"] == "drafted", "writing content promotes the status"

    def test_bilingual_status_flags_untranslated_sections(self, client, api_project):
        client.patch(
            f"/api/writing/{api_project}/sections/method",
            json={"content": "English only content here."},
        )
        payload = client.get(f"/api/writing/{api_project}/bilingual").json()
        assert payload["summary"]["untranslated"] >= 1

    def test_sync_conflict_is_explicit_and_recoverable(self, client, api_project):
        status = client.get(f"/api/writing/{api_project}/sync-status").json()
        assert status["state"] == "in_sync"
        manuscript = Path(status["path"])
        target_name = next(
            name for name in status["disk"]["files"] if "-abstract." in name
        )
        target = manuscript / target_name
        target.write_text("# Abstract\n\nchanged outside PaperCreator\n", encoding="utf-8")

        save = client.patch(
            f"/api/writing/{api_project}/sections/abstract",
            json={"content": "changed in the UI"},
        )
        assert save.status_code == 409
        error = save.json()["error"]
        assert error["code"] == "manuscript_sync_conflict"
        assert error["details"]["sync"]["disk_changed"] is True
        assert "changed outside" in target.read_text(encoding="utf-8")

        diverged = client.get(
            f"/api/writing/{api_project}/sync-status"
        ).json()
        assert diverged["state"] == "diverged"

        resolved = client.post(
            f"/api/writing/{api_project}/flush", params={"force": True}
        )
        assert resolved.status_code == 200
        payload = resolved.json()
        assert payload["sync"]["state"] == "in_sync"
        backup = Path(payload["safety_backup"]["path"])
        assert backup.is_dir()
        assert "changed in the UI" in target.read_text(encoding="utf-8")

    def test_forced_file_import_always_creates_database_snapshot(
        self, client, api_project
    ):
        status = client.get(f"/api/writing/{api_project}/sync-status").json()
        manuscript = Path(status["path"])
        target_name = next(
            name for name in status["disk"]["files"] if "-method." in name
        )
        target = manuscript / target_name
        target.write_text("# Method\n\nexternal method text\n", encoding="utf-8")

        response = client.post(
            f"/api/writing/{api_project}/reindex", params={"force": True}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["safety_snapshot"]["id"].startswith("snp_")
        assert payload["sync"]["state"] == "in_sync"
        assert client.get(
            f"/api/writing/{api_project}/sections/method"
        ).json()["content"] == "external method text"

    def test_unknown_section_is_404(self, client, api_project):
        response = client.patch(
            f"/api/writing/{api_project}/sections/not-a-section",
            json={"content": "x"},
        )
        assert response.status_code == 404

    def test_template_replace_refuses_to_destroy_content(self, client, api_project):
        """Overwriting drafted text must require explicit deletion first."""
        client.patch(
            f"/api/writing/{api_project}/sections/introduction",
            json={"content": "Work that must not be lost."},
        )
        response = client.post(
            f"/api/writing/{api_project}/template",
            json={"template_id": "survey", "replace": True},
        )
        assert response.status_code == 409
        assert "already contain text" in response.json()["error"]["message"]

    def test_reorder_rejects_unknown_keys(self, client, api_project):
        response = client.post(
            f"/api/writing/{api_project}/reorder",
            json={"section_keys": ["introduction", "not-real"]},
        )
        assert response.status_code == 422


class TestLibraryRoutes:
    def test_add_an_idea_and_find_it(self, client):
        response = client.post(
            "/api/library/papers",
            json={
                "title": "My novel research idea",
                "abstract": "Using multi-agent systems for survey writing.",
                "origin": "idea",
            },
        )
        assert response.status_code == 200
        paper_id = response.json()["paper"]["id"]

        listing = client.get("/api/library", params={"origin": "idea"}).json()
        assert any(item["id"] == paper_id for item in listing["items"])

        client.delete(f"/api/library/{paper_id}")

    def test_invalid_origin_is_rejected(self, client):
        response = client.post(
            "/api/library/papers", json={"title": "x", "origin": "not-a-kind"}
        )
        assert response.status_code == 422

    def test_a_paper_with_no_title_or_abstract_is_rejected(self, client):
        response = client.post("/api/library/papers", json={"title": "   "})
        assert response.status_code == 422

    def test_import_rejects_an_unsupported_extension(self, client):
        response = client.post(
            "/api/library/import", json={"path": "/tmp/nope.docx"}
        )
        assert response.status_code == 422

    def test_stats_shape(self, client):
        payload = client.get("/api/library/stats").json()
        assert "library" in payload and "tags" in payload
        assert "total" in payload["library"]


class TestAnalysisRoutes:
    def test_analysis_without_papers_is_refused_with_advice(self, client):
        project_id = client.post(
            "/api/projects",
            json={"title": "Empty Analysis", "git_enabled": False,
                  "apply_template": False},
        ).json()["project"]["id"]
        response = client.post("/api/analysis", json={"project_id": project_id})
        assert response.status_code == 422
        assert "search" in response.json()["error"]["message"].lower()
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_analysis_needs_a_target(self, client):
        assert client.post("/api/analysis", json={}).status_code == 422

    def test_capabilities_lists_gap_detectors(self, client):
        payload = client.get("/api/analysis/capabilities").json()
        assert len(payload["gap_detectors"]) == 5
        for detector in payload["gap_detectors"]:
            assert detector["strength"], "each detector must state its strength"
        backends = {
            backend["id"]: backend for backend in payload["embedding_backends"]
        }
        assert backends["tfidf"]["portable"] is False
        assert backends["hashing"]["portable"] is True

    def test_position_result_is_part_of_the_openapi_contract(self, client):
        schema = client.get("/api/openapi.json").json()
        position = schema["components"]["schemas"]["PositionResult"]
        assert position["required"] == [
            "paper_id", "analysis_id", "point", "method"
        ]
        assert position["properties"]["method"]["enum"] == [
            "exact_transform", "interpolated"
        ]
        response = schema["paths"][
            "/api/analysis/{analysis_id}/place-idea"
        ]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert response["$ref"].endswith("/PositionResult")

    def test_missing_analysis_is_404(self, client):
        assert client.get("/api/analysis/ana_nope").status_code == 404


class TestAgentRoutes:
    def test_pipelines_and_roles_are_described(self, client):
        payload = client.get("/api/agents/pipelines").json()
        ids = {pipeline["id"] for pipeline in payload["pipelines"]}
        assert ids == {"full_auto", "section", "stitch", "custom"}
        for pipeline in payload["pipelines"]:
            assert pipeline["name_zh"], "the UI shows Chinese labels"
            assert pipeline["steps"]

    def test_run_without_an_llm_fails_with_actionable_advice(self, client):
        """Must not hang or 500: the fix is a settings change."""
        project_id = client.post(
            "/api/projects",
            json={"title": "Agent Test", "git_enabled": False,
                  "apply_template": True},
        ).json()["project"]["id"]
        response = client.post(
            "/api/agents/run", json={"project_id": project_id, "pipeline": "full_auto"}
        )
        # 422 when no provider is configured (the usual test environment);
        # a queued job when the developer has keys in their .env.
        assert response.status_code in (200, 422)
        if response.status_code == 422:
            message = response.json()["error"]["message"]
            assert "Settings" in message or "provider" in message
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_unknown_pipeline_is_rejected(self, client):
        project_id = client.post(
            "/api/projects",
            json={"title": "Bad Pipeline", "git_enabled": False},
        ).json()["project"]["id"]
        response = client.post(
            "/api/agents/run", json={"project_id": project_id, "pipeline": "nonsense"}
        )
        assert response.status_code == 422
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_custom_pipeline_requires_roles(self, client):
        project_id = client.post(
            "/api/projects", json={"title": "Custom", "git_enabled": False}
        ).json()["project"]["id"]
        response = client.post(
            "/api/agents/run", json={"project_id": project_id, "pipeline": "custom"}
        )
        assert response.status_code == 422
        assert "custom_roles" in response.json()["error"]["message"]
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_prompt_preview_works_without_an_llm(self, client):
        """Prompt inspection must not require a configured model."""
        project_id = client.post(
            "/api/projects",
            json={"title": "Preview Test", "idea": "A specific research idea.",
                  "git_enabled": False, "apply_template": True},
        ).json()["project"]["id"]
        response = client.post(
            "/api/agents/preview",
            json={"project_id": project_id, "role": "writer",
                  "section_key": "introduction"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["system_prompt"], "no system prompt built"
        assert "A specific research idea" in payload["user_prompt"]
        assert payload["estimated_tokens"]["total"] > 0
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_preview_rejects_an_unknown_role(self, client):
        project_id = client.post(
            "/api/projects", json={"title": "Role Test", "git_enabled": False}
        ).json()["project"]["id"]
        response = client.post(
            "/api/agents/preview", json={"project_id": project_id, "role": "nope"}
        )
        assert response.status_code == 422
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_human_quality_rubric_is_append_only_and_acceptance_is_guarded(self, client):
        from papercreator.agents import quality
        from papercreator.agents.base import Blackboard
        from papercreator.core.models import Paper
        from papercreator.store import projects as projects_store
        from papercreator.store import runs as runs_store

        project_id = client.post(
            "/api/projects", json={"title": "Quality Review", "git_enabled": False}
        ).json()["project"]["id"]
        run_id = runs_store.create_run(project_id=project_id, pipeline="section")
        runs_store.start_run(run_id)
        paper = Paper(
            id="pap_quality_source",
            title="Quality source",
            abstract="This source contains the evidence used by the claim.",
            year=2026,
        )
        board = Blackboard(
            project=projects_store.require(project_id),
            papers=[paper],
            sections={
                "introduction": (
                    "The frozen introduction records a supported claim "
                    "[SOURCE2026]."
                )
            },
            citations={"introduction": [paper.id]},
            modified_section_keys={"introduction"},
            outline=[{"key": "introduction", "target_words": 10}],
            extra={"citation_keys": {paper.id: "SOURCE2026"}},
        )
        report = quality.build_quality_report(board, citation_papers=[paper])
        manuscript_snapshot = quality.build_manuscript_snapshot(
            board, source_snapshot_id="snp_quality_after"
        )
        runs_store.finish_run(
            run_id,
            result={
                "quality_report": report,
                "review_manuscript": manuscript_snapshot,
            },
        )
        base = {
            "reviewer": "Research lead",
            "factual_grounding": 4,
            "citation_support": 4,
            "methodological_soundness": 3,
            "literature_coverage": 4,
            "argument_coherence": 5,
            "writing_clarity": 4,
            "reviewed_section_keys": ["introduction", "introduction", ""],
            "reviewed_paper_ids": ["pap_quality_source", "pap_quality_source", ""],
            "reviewed_manuscript_fingerprint": manuscript_snapshot[
                "manuscript_fingerprint"
            ],
            "notes": "Checked the cited source against the claim.",
        }
        blocked = client.post(
            f"/api/agents/runs/{run_id}/evaluations",
            json={**base, "decision": "accepted", "source_evidence_checked": False},
        )
        assert blocked.status_code == 422
        assert blocked.json()["error"]["code"] == (
            "agent_evaluation_acceptance_requirements"
        )

        revision = client.post(
            f"/api/agents/runs/{run_id}/evaluations",
            json={
                **base,
                "decision": "revision_required",
                "source_evidence_checked": False,
            },
        )
        assert revision.status_code == 200, revision.text
        accepted = client.post(
            f"/api/agents/runs/{run_id}/evaluations",
            json={**base, "decision": "accepted", "source_evidence_checked": True},
        )
        assert accepted.status_code == 200, accepted.text
        result = accepted.json()["result"]
        assert [item["decision"] for item in result["human_evaluations"]] == [
            "revision_required", "accepted"
        ]
        latest = result["latest_human_evaluation"]
        assert latest["rubric_version"] == 3
        assert latest["overall_score"] == 4.0
        assert latest["reviewed_section_keys"] == ["introduction"]
        assert latest["reviewed_paper_ids"] == ["pap_quality_source"]
        assert len(latest["review_target"]["quality_report_fingerprint"]) == 64
        assert latest["review_target"]["manuscript_integrity"] == "pass"
        assert latest["review_target"]["manuscript_fingerprint"] == (
            manuscript_snapshot["manuscript_fingerprint"]
        )
        assert result["quality_report"]["acceptance"] == {
            "automatic_gate": "pass",
            "human_review_required": False,
            "semantic_grounding_verified": False,
            "latest_human_decision": "accepted",
            "human_review_recorded": True,
            "latest_human_evaluation_id": latest["id"],
            "latest_human_rubric_version": 3,
            "human_source_evidence_checked": True,
        }
        summary = client.get(
            "/api/agents/evaluations/summary", params={"project_id": project_id}
        )
        assert summary.status_code == 200, summary.text
        aggregate = summary.json()
        assert aggregate["reviewed_runs"] == 1
        assert aggregate["evaluation_records"] == 2
        assert aggregate["multi_reviewed_runs"] == 1
        assert aggregate["decision_disagreement_runs"] == 1
        assert aggregate["latest_decisions"]["accepted"] == 1
        assert aggregate["dimensions"]["factual_grounding"]["average"] == 4.0
        assert aggregate["by_pipeline"][0]["label"] == "section"
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_acceptance_cannot_override_failed_run_or_structural_gate(self, client):
        from papercreator.store import runs as runs_store

        project_id = client.post(
            "/api/projects", json={"title": "Strict Acceptance", "git_enabled": False}
        ).json()["project"]["id"]
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
            "reviewed_paper_ids": ["pap_source"],
            "notes": "Opened the source and recorded why the result is supported.",
        }

        def report(gate: str) -> dict:
            return {
                "schema_version": 1,
                "generated_at": "2026-07-28T00:00:00Z",
                "run_status": "done",
                "gate": gate,
                "sections": [{"section_key": "results", "modified_by_run": True}],
                "citation_registry": {"cited_paper_ids": ["pap_source"]},
            }

        failed_id = runs_store.create_run(project_id=project_id, pipeline="section")
        runs_store.finish_run(
            failed_id, status="failed", result={"quality_report": report("pass")}
        )
        failed = client.post(f"/api/agents/runs/{failed_id}/evaluations", json=payload)
        assert failed.status_code == 422
        assert "run_not_done" in failed.json()["error"]["details"]["blockers"]

        gated_id = runs_store.create_run(project_id=project_id, pipeline="section")
        runs_store.finish_run(gated_id, result={"quality_report": report("fail")})
        gated = client.post(f"/api/agents/runs/{gated_id}/evaluations", json=payload)
        assert gated.status_code == 422
        assert "automatic_gate_not_acceptable" in (
            gated.json()["error"]["details"]["blockers"]
        )
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_review_targets_must_belong_to_the_quality_report(self, client):
        from papercreator.store import runs as runs_store

        project_id = client.post(
            "/api/projects", json={"title": "Review Targets", "git_enabled": False}
        ).json()["project"]["id"]
        run_id = runs_store.create_run(project_id=project_id, pipeline="section")
        runs_store.finish_run(
            run_id,
            result={
                "quality_report": {
                    "schema_version": 1,
                    "generated_at": "2026-07-28T00:00:00Z",
                    "gate": "warn",
                    "sections": [{"section_key": "method", "modified_by_run": True}],
                    "citation_registry": {"cited_paper_ids": ["pap_known"]},
                }
            },
        )
        response = client.post(
            f"/api/agents/runs/{run_id}/evaluations",
            json={
                "decision": "revision_required",
                "factual_grounding": 3,
                "citation_support": 3,
                "methodological_soundness": 3,
                "literature_coverage": 3,
                "argument_coherence": 3,
                "writing_clarity": 3,
                "reviewed_section_keys": ["invented"],
                "reviewed_paper_ids": ["pap_unknown"],
            },
        )
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "agent_evaluation_unknown_review_targets"
        assert error["details"]["unknown_section_keys"] == ["invented"]
        assert error["details"]["unknown_paper_ids"] == ["pap_unknown"]
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_running_agent_run_cannot_be_reviewed(self, client):
        from papercreator.store import runs as runs_store

        project_id = client.post(
            "/api/projects", json={"title": "Running Review", "git_enabled": False}
        ).json()["project"]["id"]
        run_id = runs_store.create_run(project_id=project_id, pipeline="section")
        payload = {
            "decision": "revision_required",
            "factual_grounding": 3,
            "citation_support": 3,
            "methodological_soundness": 3,
            "literature_coverage": 3,
            "argument_coherence": 3,
            "writing_clarity": 3,
        }
        response = client.post(
            f"/api/agents/runs/{run_id}/evaluations", json=payload
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "agent_run_not_terminal"
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})


class TestSkillRoutes:
    def test_builtin_skills_are_registered(self, client):
        payload = client.get("/api/skills").json()
        ids = {skill["id"] for skill in payload["items"]}
        assert "evidence-discipline" in ids, "the safety skill must ship"
        assert "ieee-conference-style" in ids
        assert payload["valid_roles"]

    def test_builtins_are_read_only(self, client):
        payload = client.get("/api/skills").json()
        builtin = next(s for s in payload["items"] if s["scope"] == "builtin")
        assert builtin["editable"] is False
        response = client.delete(f"/api/skills/{builtin['id']}")
        assert response.status_code == 409
        assert "builtin" in response.json()["error"]["message"]

    def test_save_and_preview_a_user_skill(self, client):
        response = client.post(
            "/api/skills",
            json={
                "name": "Test Convention",
                "instructions": "Always use British spelling. Never use contractions.",
                "applies_to": ["writer"],
                "triggers": ["british"],
                "overwrite": True,
            },
        )
        assert response.status_code == 200, response.text
        skill_id = response.json()["skill"]["id"]

        preview = client.post(
            "/api/skills/preview", json={"skill_ids": [skill_id], "role": "writer"}
        ).json()
        assert "British spelling" in preview["text"]
        assert preview["estimated_tokens"] > 0

        # A skill scoped to the writer must not appear in the planner's prompt.
        other = client.post(
            "/api/skills/preview", json={"skill_ids": [skill_id], "role": "planner"}
        ).json()
        assert other["text"] == ""

        client.delete(f"/api/skills/{skill_id}")

    def test_a_skill_without_instructions_is_rejected(self, client):
        response = client.post(
            "/api/skills", json={"name": "Empty", "instructions": "   "}
        )
        assert response.status_code == 422

    def test_suggest_matches_triggers(self, client):
        payload = client.post(
            "/api/skills/suggest",
            json={"text": "I am writing an IEEE conference paper about GNNs"},
        ).json()
        assert any(
            entry["id"] == "ieee-conference-style" for entry in payload["suggestions"]
        ), payload


class TestExportRoutes:
    def test_capabilities_are_reported(self, client):
        payload = client.get("/api/export/capabilities").json()
        assert payload["formats"]
        assert "pandoc" in payload and "latex_engines" in payload

    def test_unknown_format_is_rejected(self, client):
        project_id = client.post(
            "/api/projects", json={"title": "Export Test", "git_enabled": False}
        ).json()["project"]["id"]
        response = client.post(
            f"/api/export/{project_id}", json={"format": "pdf-ultra"}
        )
        assert response.status_code == 422
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_download_refuses_a_path_outside_the_project(self, client):
        """Otherwise this endpoint would be an arbitrary file read."""
        project_id = client.post(
            "/api/projects", json={"title": "Path Test", "git_enabled": False}
        ).json()["project"]["id"]
        response = client.get(
            f"/api/export/{project_id}/download",
            params={"path": "C:/Windows/System32/drivers/etc/hosts"},
        )
        assert response.status_code == 422
        assert "outside the project" in response.json()["error"]["message"]
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_markdown_to_latex_conversion_endpoint(self, client):
        response = client.post(
            "/api/export/convert",
            json={"text": "# Title\n\n**bold**", "direction": "md2tex"},
        )
        assert response.status_code == 200
        assert r"\section{Title}" in response.json()["result"]

    def test_conversion_direction_is_validated(self, client):
        response = client.post(
            "/api/export/convert", json={"text": "x", "direction": "sideways"}
        )
        assert response.status_code == 422


class TestVersionRoutes:
    def test_timeline_works_without_a_git_repository(self, client):
        project_id = client.post(
            "/api/projects", json={"title": "Version Test", "git_enabled": False}
        ).json()["project"]["id"]
        payload = client.get(f"/api/versions/{project_id}").json()
        assert "entries" in payload and "git" in payload
        assert payload["git"]["is_repo"] is False
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_snapshot_and_diff(self, client):
        project_id = client.post(
            "/api/projects",
            json={"title": "Snapshot Test", "git_enabled": False,
                  "apply_template": True},
        ).json()["project"]["id"]
        client.patch(
            f"/api/writing/{project_id}/sections/introduction",
            json={"content": "first version of the text"},
        )
        snapshot_id = client.post(
            f"/api/versions/{project_id}/snapshots", json={"label": "v1"}
        ).json()["id"]
        client.patch(
            f"/api/writing/{project_id}/sections/introduction",
            json={"content": "a completely rewritten version"},
        )
        diff = client.get(
            f"/api/versions/{project_id}/compare",
            params={"left": snapshot_id, "right": "current"},
        ).json()
        assert diff["mode"] == "snapshot"
        assert diff["summary"]["modified"] == 1
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_manual_git_init_enables_later_version_commits(self, client):
        from papercreator.vcs import git as git_module

        if not git_module.git_available():
            pytest.skip("Git is not installed")
        project_id = client.post(
            "/api/projects",
            json={"title": "Manual Git Test", "git_enabled": False,
                  "apply_template": True},
        ).json()["project"]["id"]
        try:
            initialised = client.post(
                f"/api/versions/{project_id}/git/init"
            )
            assert initialised.status_code == 200
            assert initialised.json()["git_enabled"] is True
            client.patch(
                f"/api/writing/{project_id}/sections/introduction",
                json={"content": "version committed after manual Git opt-in"},
            )
            saved = client.post(
                f"/api/versions/{project_id}/save",
                json={"label": "manual init commit", "commit_message": "commit"},
            )
            assert saved.status_code == 200
            assert saved.json()["git"]["committed"] is True
        finally:
            client.delete(
                f"/api/projects/{project_id}", params={"remove_files": True}
            )

    def test_discard_requires_confirmation(self, client):
        """An irreversible operation must not happen on a bare call."""
        project_id = client.post(
            "/api/projects", json={"title": "Discard Test", "git_enabled": False}
        ).json()["project"]["id"]
        response = client.post(f"/api/versions/{project_id}/git/discard")
        # 409 when confirmation is missing, 400/500 when git is absent entirely.
        assert response.status_code in (409, 400, 500)
        client.delete(f"/api/projects/{project_id}", params={"remove_files": True})

    def test_confirmed_discard_is_recoverable_and_reindexes_database(self, client):
        """Tracked text is backed up before Git wins over the DB mirror."""
        from papercreator.store import projects as projects_store
        from papercreator.vcs import git as git_module

        if not git_module.git_available():
            pytest.skip("Git is not installed")
        project_id = client.post(
            "/api/projects",
            json={"title": "Safe Discard Test", "git_enabled": True,
                  "apply_template": True, "template_id": "generic"},
        ).json()["project"]["id"]
        try:
            project = projects_store.require(project_id)
            untracked = projects_store.project_root(project) / "notes" / "keep.txt"
            untracked.parent.mkdir(parents=True, exist_ok=True)
            untracked.write_text("must survive Git discard", encoding="utf-8")
            before = client.get(
                f"/api/versions/{project_id}/git/status"
            ).json()
            assert before["untracked"] == ["notes/keep.txt"]

            saved = client.patch(
                f"/api/writing/{project_id}/sections/introduction",
                json={"content": "uncommitted text that Git will discard"},
            )
            assert saved.status_code == 200

            response = client.post(
                f"/api/versions/{project_id}/git/discard",
                params={"confirm": True},
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["discarded"] is True
            assert Path(payload["git_patch"]["path"]).stat().st_size > 0
            assert ".papercreator/conflicts/" in payload["git_patch"][
                "restore_command"
            ]
            assert Path(payload["manuscript_backup"]["path"]).is_dir()
            assert payload["safety_snapshot"]["id"].startswith("snp_")
            assert payload["reindexed"]["sync"]["state"] == "in_sync"
            assert untracked.read_text(encoding="utf-8") == "must survive Git discard"
            section = client.get(
                f"/api/writing/{project_id}/sections/introduction"
            ).json()
            assert section["content"] == ""
        finally:
            client.delete(
                f"/api/projects/{project_id}", params={"remove_files": True}
            )

    def test_checkout_refuses_to_overwrite_database_only_change(self, client):
        from papercreator.store import documents as documents_store
        from papercreator.vcs import git as git_module

        if not git_module.git_available():
            pytest.skip("Git is not installed")
        project_id = client.post(
            "/api/projects",
            json={"title": "Safe Checkout Test", "git_enabled": True,
                  "apply_template": True, "template_id": "generic"},
        ).json()["project"]["id"]
        try:
            document = documents_store.primary_document(project_id)
            section = documents_store.get_section_by_key(
                document.id, "introduction"
            )
            assert section is not None
            documents_store.update_section(
                section.id, content="database-only text that must survive"
            )
            response = client.post(
                f"/api/versions/{project_id}/git/checkout", json={"ref": "main"}
            )
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "manuscript_sync_conflict"
            assert documents_store.require_section(section.id).content == (
                "database-only text that must survive"
            )
        finally:
            client.delete(
                f"/api/projects/{project_id}", params={"remove_files": True}
            )

    def test_remote_pull_fast_forwards_with_recovery_and_reindexes(
        self, client, temp_home
    ):
        """Remote files only replace the DB mirror after recovery is captured."""
        from papercreator.store import projects as projects_store
        from papercreator.vcs import git as git_module

        if not git_module.git_available():
            pytest.skip("Git is not installed")
        project_id = client.post(
            "/api/projects",
            json={"title": "Remote Pull Test", "git_enabled": True,
                  "apply_template": True, "template_id": "generic"},
        ).json()["project"]["id"]
        remote = temp_home / f"api remote {project_id}.git"
        peer = temp_home / f"api peer {project_id}"
        try:
            project = projects_store.require(project_id)
            directory = projects_store.project_root(project)
            remote.mkdir()
            git_module.run(["init", "--bare"], remote, check=True)
            git_module.run(
                ["symbolic-ref", "HEAD", "refs/heads/main"], remote, check=True
            )
            configured = client.post(
                f"/api/versions/{project_id}/git/remote",
                json={"url": str(remote)},
            )
            assert configured.status_code == 200
            pushed = client.post(f"/api/versions/{project_id}/git/push")
            assert pushed.status_code == 200

            sync = client.get(f"/api/writing/{project_id}/sync-status").json()
            target_name = next(
                name for name in sync["disk"]["files"]
                if "-introduction." in name
            )
            target = Path(sync["path"]) / target_name
            relative_target = target.relative_to(directory)

            git_module.run(["clone", str(remote), str(peer)], temp_home, check=True)
            git_module.run(["config", "user.name", "Remote Peer"], peer, check=True)
            git_module.run(
                ["config", "user.email", "peer@localhost"], peer, check=True
            )
            peer_target = peer / relative_target
            peer_target.write_text(
                "# Introduction\n\nremote collaborator text\n", encoding="utf-8"
            )
            git_module.run(
                ["add", "--", relative_target.as_posix()], peer, check=True
            )
            git_module.run(["commit", "-m", "remote introduction"], peer, check=True)
            git_module.run(["push", "origin", "main"], peer, check=True)

            fetched = client.post(f"/api/versions/{project_id}/git/fetch")
            assert fetched.status_code == 200
            assert fetched.json()["sync"]["state"] == "behind"
            before = client.get(
                f"/api/writing/{project_id}/sections/introduction"
            ).json()
            assert "remote collaborator text" not in before["content"]

            pulled = client.post(f"/api/versions/{project_id}/git/pull")
            assert pulled.status_code == 200
            payload = pulled.json()
            assert payload["updated"] is True
            assert payload["commits"] == 1
            assert payload["sync"]["state"] == "up_to_date"
            assert payload["safety_snapshot"]["id"].startswith("snp_")
            assert Path(payload["manuscript_backup"]["path"]).is_dir()
            assert payload["reindexed"]["sync"]["state"] == "in_sync"
            after = client.get(
                f"/api/writing/{project_id}/sections/introduction"
            ).json()
            assert after["content"] == "remote collaborator text"
            head_before = git_module.run(
                ["rev-parse", "HEAD"], directory, check=True
            ).stdout.strip()
            removed = client.delete(
                f"/api/versions/{project_id}/git/remote",
                params={"name": "origin"},
            )
            assert removed.status_code == 200
            assert removed.json()["removed"] is True
            assert client.get(
                f"/api/versions/{project_id}/git/remotes"
            ).json() == {"remotes": []}
            assert git_module.run(
                ["rev-parse", "HEAD"], directory, check=True
            ).stdout.strip() == head_before
        finally:
            client.delete(
                f"/api/projects/{project_id}", params={"remove_files": True}
            )
            shutil.rmtree(remote, ignore_errors=True)
            shutil.rmtree(peer, ignore_errors=True)

    def test_snapshot_restore_refuses_changed_manuscript_file(self, client):
        project_id = client.post(
            "/api/projects",
            json={"title": "Safe Restore Test", "git_enabled": False,
                  "apply_template": True, "template_id": "generic"},
        ).json()["project"]["id"]
        try:
            snapshot_id = client.post(
                f"/api/versions/{project_id}/snapshots", json={"label": "empty"}
            ).json()["id"]
            status = client.get(
                f"/api/writing/{project_id}/sync-status"
            ).json()
            target_name = next(
                name for name in status["disk"]["files"]
                if "-introduction." in name
            )
            target = Path(status["path"]) / target_name
            target.write_text(
                "# Introduction\n\nexternal editor text\n", encoding="utf-8"
            )

            response = client.post(
                f"/api/versions/{project_id}/restore",
                json={"ref": snapshot_id},
            )
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "manuscript_sync_conflict"
            assert "external editor text" in target.read_text(encoding="utf-8")
        finally:
            client.delete(
                f"/api/projects/{project_id}", params={"remove_files": True}
            )


class TestSearchRoutes:
    def test_providers_are_listed_with_capabilities(self, client):
        payload = client.get("/api/search/providers").json()
        assert len(payload["providers"]) >= 8
        for provider in payload["providers"]:
            assert "capabilities" in provider
            assert "rate_limit" in provider

    def test_keyword_search_requires_a_query(self, client):
        response = client.post("/api/search", json={"query": "", "mode": "keyword"})
        assert response.status_code == 422

    def test_idea_search_requires_seed_text(self, client):
        response = client.post("/api/search", json={"mode": "idea", "seed_text": ""})
        assert response.status_code == 422
        assert "seed_text" in response.json()["error"]["message"]

    def test_background_search_preserves_llm_expansion_choice(
        self, client, monkeypatch
    ):
        from papercreator.retrieval import pipeline

        captured = {}

        def fake_submit(request):
            captured["request"] = request
            return "job_expansion_contract"

        monkeypatch.setattr(pipeline, "submit_search", fake_submit)
        response = client.post(
            "/api/search",
            json={
                "mode": "idea",
                "seed_text": "graph learning for molecules",
                "providers": ["local"],
                "use_llm_expansion": False,
            },
        )
        assert response.status_code == 200
        assert captured["request"].use_llm_expansion is False
        assert captured["request"].model_dump()["use_llm_expansion"] is False

    def test_history_rerun_preserves_expansion_choice(self, client, monkeypatch):
        from papercreator.core.models import SearchRequest
        from papercreator.retrieval import pipeline
        from papercreator.store import papers as papers_store

        original = SearchRequest(
            mode="paper",
            seed_text="a saved paper abstract",
            providers=["local"],
            use_cache=True,
            use_llm_expansion=False,
        )
        search_id = papers_store.record_search(
            query_text="",
            mode="paper",
            seed_text=original.seed_text,
            providers=["local"],
            params=original.model_dump(),
            papers=[],
            provider_stats={},
        )
        captured = {}

        def fake_submit(request):
            captured["request"] = request
            return "job_history_contract"

        monkeypatch.setattr(pipeline, "submit_search", fake_submit)
        try:
            response = client.post(
                f"/api/search/history/{search_id}/rerun",
                params={"use_cache": False},
            )
            assert response.status_code == 200
            rerun = captured["request"]
            assert rerun.use_llm_expansion is False
            assert rerun.use_cache is False
            assert rerun.seed_text == original.seed_text
        finally:
            papers_store.delete_search(search_id)

    def test_rule_based_expansion_needs_no_llm(self, client):
        response = client.post(
            "/api/search/expand",
            json={
                "seed_text": "Multi-agent language models that write survey papers "
                             "by splitting retrieval and drafting across agents.",
                "use_llm": False,
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["queries"]
        assert payload["method"] == "rules"

    def test_expansion_requires_input(self, client):
        assert client.post("/api/search/expand", json={}).status_code == 422

    def test_resolve_requires_an_identifier(self, client):
        response = client.post("/api/search/resolve", json={"identifier": "  "})
        assert response.status_code == 422

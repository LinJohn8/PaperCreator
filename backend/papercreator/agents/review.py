"""Blind-review and analysis packets for Agent quality evidence.

The two packet kinds intentionally have different disclosure contracts. A blind
packet contains the frozen prose, automatic checks and source evidence needed
to score it, but no run/project/model/pipeline/reviewer identity or prior human
decision. An analysis packet restores provenance, cost and append-only reviews
for research-quality benchmarking after labels have been collected.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from ..core.util import sha256_text, utc_now_iso
from .quality import build_review_target

REVIEW_PACKET_SCHEMA_VERSION = 1


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _redact(value: Any) -> Any:
    """Defense-in-depth redaction for future request fields that may be secret."""

    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).casefold()
            if any(token in lowered for token in ("api_key", "secret", "password", "token")):
                output[str(key)] = "[REDACTED]"
            else:
                output[str(key)] = _redact(item)
        return output
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def build_packet(
    run: dict[str, Any],
    *,
    source_evidence: list[dict[str, Any]] | None = None,
    steps: list[dict[str, Any]] | None = None,
    kind: str = "blind",
) -> dict[str, Any]:
    """Build a self-contained, fingerprinted review or analysis packet."""

    if kind not in {"blind", "analysis"}:
        raise ValueError("review packet kind must be 'blind' or 'analysis'")
    result = run.get("result") if isinstance(run.get("result"), dict) else {}
    report = result.get("quality_report")
    report = deepcopy(report) if isinstance(report, dict) else {}
    manuscript = result.get("review_manuscript")
    manuscript = deepcopy(manuscript) if isinstance(manuscript, dict) else {}
    review_target = build_review_target(
        report,
        run_status=str(run.get("status") or ""),
        manuscript_snapshot=manuscript,
    )
    manuscript_fingerprint = str(review_target.get("manuscript_fingerprint") or "")
    fallback = str(review_target.get("quality_report_fingerprint") or "")
    sample_id = f"sample_{(manuscript_fingerprint or fallback)[:24]}"

    # A previous human decision is a powerful source of anchoring bias and has
    # no place in a blind packet. The automatic gate remains because reviewers
    # need to know which deterministic issues were observed.
    report.pop("acceptance", None)
    packet_sources = deepcopy(source_evidence or [])
    if kind == "blind":
        manuscript.pop("source_snapshot_id", None)
        review_target.pop("manuscript_source_snapshot_id", None)
        registry = report.get("citation_registry")
        registry = registry if isinstance(registry, dict) else {}
        registry_papers = registry.get("papers")
        if isinstance(registry_papers, list):
            for paper in registry_papers:
                if isinstance(paper, dict):
                    paper.pop("pdf_path", None)
        for paper in packet_sources:
            if isinstance(paper, dict):
                paper.pop("pdf_path", None)
    packet: dict[str, Any] = {
        "schema_version": REVIEW_PACKET_SCHEMA_VERSION,
        "packet_kind": kind,
        "generated_at": utc_now_iso(),
        "sample_id": sample_id,
        "identity_hidden": kind == "blind",
        "evidence_contract": review_target,
        "manuscript": manuscript,
        "automatic_quality_report": report,
        "source_evidence": packet_sources,
    }
    if kind == "analysis":
        step_rows = steps or []
        models = sorted(
            {
                str(step.get("model") or "")
                for step in step_rows
                if str(step.get("model") or "")
            }
        )
        roles = [
            {
                "ordering": int(step.get("ordering") or 0),
                "agent": str(step.get("agent") or ""),
                "model": str(step.get("model") or ""),
                "status": str(step.get("status") or ""),
            }
            for step in step_rows
        ]
        packet["provenance"] = {
            "run_id": str(run.get("id") or ""),
            "project_id": str(run.get("project_id") or ""),
            "pipeline": str(run.get("pipeline") or ""),
            "mode": str(run.get("mode") or ""),
            "status": str(run.get("status") or ""),
            "request": _redact(run.get("request") or {}),
            "models": models,
            "roles": roles,
            "tokens_in": int(run.get("tokens_in") or 0),
            "tokens_out": int(run.get("tokens_out") or 0),
            "cost_usd": float(run.get("cost_usd") or 0.0),
            "created_at": str(run.get("created_at") or ""),
            "started_at": str(run.get("started_at") or ""),
            "finished_at": str(run.get("finished_at") or ""),
        }
        evaluations = result.get("human_evaluations")
        packet["human_evaluations"] = _redact(
            evaluations if isinstance(evaluations, list) else []
        )

    fingerprint_basis = deepcopy(packet)
    fingerprint_basis.pop("generated_at", None)
    packet["packet_fingerprint"] = sha256_text(_canonical(fingerprint_basis))
    return packet

"""Agent run/step records and the LLM usage ledger.

Every LLM call in the app writes a row to ``llm_usage``; every agent step writes
``agent_steps`` including the *full prompt and response*. That is deliberate:
when an agent produces a bad section the user needs to see exactly what it was
told, and prompt archaeology after the fact is otherwise impossible.

Prompts are large. :func:`prune_step_prompts` exists to drop the prompt text of
old runs while keeping the outputs and the accounting.
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import Any

from ..core.db import dumps, execute, loads, query, query_one, row_to_dict, transaction
from ..core.errors import NotFoundError
from ..core.util import new_id, utc_now_iso

# ---------------------------------------------------------------------- runs


def create_run(
    *,
    project_id: str,
    pipeline: str,
    mode: str = "",
    request: dict[str, Any] | None = None,
    run_id: str = "",
) -> str:
    rid = run_id or new_id("run")
    execute(
        "INSERT INTO agent_runs (id, project_id, pipeline, mode, status, request,"
        " created_at) VALUES (?,?,?,?,'pending',?,?)",
        (rid, project_id or None, pipeline, mode, dumps(request or {}), utc_now_iso()),
    )
    return rid


def start_run(run_id: str) -> None:
    execute(
        "UPDATE agent_runs SET status='running', started_at=? WHERE id=?",
        (utc_now_iso(), run_id),
    )


def finish_run(
    run_id: str,
    *,
    status: str = "done",
    result: dict[str, Any] | None = None,
    error: str = "",
) -> None:
    execute(
        "UPDATE agent_runs SET status=?, result=?, error=?, finished_at=? WHERE id=?",
        (status, dumps(result or {}), error, utc_now_iso(), run_id),
    )


def add_run_usage(run_id: str, tokens_in: int, tokens_out: int, cost_usd: float) -> None:
    """Accumulate token/cost totals on the run row.

    Kept as a running total on the run (rather than summed from ``llm_usage`` on
    read) so the orchestrator can enforce its budget with a single cheap read.
    """
    execute(
        "UPDATE agent_runs SET tokens_in=tokens_in+?, tokens_out=tokens_out+?,"
        " cost_usd=cost_usd+? WHERE id=?",
        (int(tokens_in), int(tokens_out), float(cost_usd), run_id),
    )


def append_human_evaluation(
    run_id: str, evaluation: dict[str, Any]
) -> dict[str, Any]:
    """Append an immutable human quality rubric to a terminal run.

    The run ``result`` JSON is used so schema-v2 workbenches need no migration.
    Appending under an IMMEDIATE transaction prevents two local reviewer actions
    from reading the same list and silently losing one another.
    """

    entry = {
        **evaluation,
        "id": new_id("eval"),
        "rubric_version": int(evaluation.get("rubric_version") or 1),
        "created_at": utc_now_iso(),
    }
    dimensions = entry.get("dimensions") or {}
    scores = [int(value) for value in dimensions.values()]
    entry["overall_score"] = round(sum(scores) / len(scores), 2) if scores else 0.0

    with transaction() as conn:
        row = conn.execute(
            "SELECT result FROM agent_runs WHERE id=?", (run_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"agent run {run_id} not found")
        result = loads(row["result"], {}) or {}
        evaluations = result.get("human_evaluations")
        evaluations = list(evaluations) if isinstance(evaluations, list) else []
        evaluations.append(entry)
        result["human_evaluations"] = evaluations
        result["latest_human_evaluation"] = entry
        quality_report = result.get("quality_report")
        if isinstance(quality_report, dict):
            acceptance = quality_report.get("acceptance")
            acceptance = dict(acceptance) if isinstance(acceptance, dict) else {}
            acceptance.update(
                {
                    "human_review_required": entry.get("decision") != "accepted",
                    "human_review_recorded": True,
                    "latest_human_decision": entry.get("decision") or "unreviewed",
                    "latest_human_evaluation_id": entry["id"],
                    "latest_human_rubric_version": entry["rubric_version"],
                    "human_source_evidence_checked": bool(
                        entry.get("source_evidence_checked")
                    ),
                }
            )
            quality_report["acceptance"] = acceptance
            result["quality_report"] = quality_report
        conn.execute(
            "UPDATE agent_runs SET result=? WHERE id=?", (dumps(result), run_id)
        )
    return entry


def get_run(run_id: str, *, with_steps: bool = True) -> dict[str, Any] | None:
    row = row_to_dict(query_one("SELECT * FROM agent_runs WHERE id=?", (run_id,)))
    if row is None:
        return None
    row["request"] = loads(row.get("request"), {}) or {}
    row["result"] = loads(row.get("result"), {}) or {}
    if with_steps:
        row["steps"] = list_steps(run_id)
    return row


def require_run(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    if run is None:
        raise NotFoundError(f"agent run {run_id} not found")
    return run


def list_runs(
    project_id: str = "", *, status: str = "", limit: int = 50
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if project_id:
        clauses.append("project_id=?")
        params.append(project_id)
    if status:
        clauses.append("status=?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = query(
        f"SELECT id, project_id, pipeline, mode, status, error, tokens_in,"
        f" tokens_out, cost_usd, started_at, finished_at, created_at,"
        f" (SELECT COUNT(*) FROM agent_steps s WHERE s.run_id = agent_runs.id)"
        f" AS step_count FROM agent_runs {where}"
        f" ORDER BY created_at DESC LIMIT ?",
        (*params, limit),
    )
    return [dict(r) for r in rows]


_EVALUATION_DIMENSIONS = (
    "factual_grounding",
    "citation_support",
    "methodological_soundness",
    "literature_coverage",
    "argument_coherence",
    "writing_clarity",
)
_EVALUATION_DECISIONS = ("accepted", "revision_required", "rejected")


def evaluation_summary(project_id: str = "", *, limit: int = 500) -> dict[str, Any]:
    """Aggregate latest human rubrics without erasing append-only history.

    Decision and dimension aggregates use the latest review for each run. The
    total record count and disagreement metrics still expose repeated reviews,
    which is necessary when building a gold set or measuring reviewer drift.
    """

    where = "WHERE r.project_id=?" if project_id else ""
    params: tuple[Any, ...] = (project_id, int(limit)) if project_id else (int(limit),)
    rows = query(
        "SELECT r.id, r.pipeline, r.request, r.result,"
        " (SELECT GROUP_CONCAT(DISTINCT NULLIF(s.model,''))"
        "  FROM agent_steps s WHERE s.run_id=r.id) AS models"
        f" FROM agent_runs r {where} ORDER BY r.created_at DESC LIMIT ?",
        params,
    )
    latest_decisions = {decision: 0 for decision in _EVALUATION_DECISIONS}
    dimension_values: dict[str, list[float]] = {
        dimension: [] for dimension in _EVALUATION_DIMENSIONS
    }
    groups: dict[str, dict[str, dict[str, Any]]] = {
        "pipeline": {},
        "model": {},
    }
    reviewed_runs = 0
    evaluation_records = 0
    multi_reviewed_runs = 0
    decision_disagreement_runs = 0
    score_spreads: list[float] = []
    reviewer_ids: set[str] = set()
    decision_pairs: list[tuple[str, str]] = []
    dimension_pairs: dict[str, list[tuple[float, float]]] = {
        dimension: [] for dimension in _EVALUATION_DIMENSIONS
    }

    def add_group(kind: str, label: str, evaluation: dict[str, Any]) -> None:
        bucket = groups[kind].setdefault(
            label,
            {
                "label": label,
                "reviewed_runs": 0,
                "score_total": 0.0,
                "score_count": 0,
                "latest_decisions": {
                    decision: 0 for decision in _EVALUATION_DECISIONS
                },
            },
        )
        bucket["reviewed_runs"] += 1
        decision = str(evaluation.get("decision") or "")
        if decision in bucket["latest_decisions"]:
            bucket["latest_decisions"][decision] += 1
        score = float(evaluation.get("overall_score") or 0.0)
        if score:
            bucket["score_total"] += score
            bucket["score_count"] += 1

    for row in rows:
        result = loads(row["result"], {}) or {}
        evaluations = result.get("human_evaluations")
        evaluations = evaluations if isinstance(evaluations, list) else []
        evaluations = [item for item in evaluations if isinstance(item, dict)]
        if not evaluations:
            continue
        for evaluation in evaluations:
            reviewer_id = str(evaluation.get("reviewer") or "").strip().casefold()
            if reviewer_id:
                reviewer_ids.add(reviewer_id)
        # Agreement deliberately uses only pairs with two distinct, identified
        # reviewers. Repeated clicks by one person and anonymous legacy records
        # are useful audit history, but are not independent reliability data.
        for left, right in combinations(evaluations, 2):
            left_reviewer = str(left.get("reviewer") or "").strip().casefold()
            right_reviewer = str(right.get("reviewer") or "").strip().casefold()
            if not left_reviewer or not right_reviewer or left_reviewer == right_reviewer:
                continue
            left_decision = str(left.get("decision") or "")
            right_decision = str(right.get("decision") or "")
            if (
                left_decision in _EVALUATION_DECISIONS
                and right_decision in _EVALUATION_DECISIONS
            ):
                decision_pairs.append((left_decision, right_decision))
            left_dimensions = left.get("dimensions")
            right_dimensions = right.get("dimensions")
            left_dimensions = left_dimensions if isinstance(left_dimensions, dict) else {}
            right_dimensions = right_dimensions if isinstance(right_dimensions, dict) else {}
            for dimension in _EVALUATION_DIMENSIONS:
                a = left_dimensions.get(dimension)
                b = right_dimensions.get(dimension)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    dimension_pairs[dimension].append((float(a), float(b)))
        reviewed_runs += 1
        evaluation_records += len(evaluations)
        if len(evaluations) >= 2:
            multi_reviewed_runs += 1
            decisions = {
                str(item.get("decision") or "") for item in evaluations
            }
            if len(decisions) > 1:
                decision_disagreement_runs += 1
            scores = [
                float(item.get("overall_score") or 0.0) for item in evaluations
            ]
            scores = [score for score in scores if score]
            if len(scores) >= 2:
                score_spreads.append(max(scores) - min(scores))

        latest = result.get("latest_human_evaluation")
        latest = latest if isinstance(latest, dict) else evaluations[-1]
        decision = str(latest.get("decision") or "")
        if decision in latest_decisions:
            latest_decisions[decision] += 1
        dimensions = latest.get("dimensions")
        dimensions = dimensions if isinstance(dimensions, dict) else {}
        for dimension in _EVALUATION_DIMENSIONS:
            value = dimensions.get(dimension)
            if isinstance(value, (int, float)):
                dimension_values[dimension].append(float(value))

        request = loads(row["request"], {}) or {}
        model_label = str(row["models"] or request.get("model") or "role defaults")
        add_group("pipeline", str(row["pipeline"] or "unknown"), latest)
        add_group("model", model_label, latest)

    def finish_groups(kind: str) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for bucket in groups[kind].values():
            count = int(bucket.pop("score_count"))
            total = float(bucket.pop("score_total"))
            bucket["average_overall_score"] = round(total / count, 2) if count else 0.0
            output.append(bucket)
        return sorted(output, key=lambda item: (-item["reviewed_runs"], item["label"]))

    dimensions = {
        dimension: {
            "count": len(values),
            "average": round(sum(values) / len(values), 2) if values else 0.0,
            "minimum": min(values) if values else 0.0,
            "maximum": max(values) if values else 0.0,
        }
        for dimension, values in dimension_values.items()
    }

    def categorical_kappa(pairs: list[tuple[str, str]]) -> float | None:
        if not pairs:
            return None
        observed = sum(a == b for a, b in pairs) / len(pairs)
        ratings = Counter(value for pair in pairs for value in pair)
        total = 2 * len(pairs)
        expected = sum((ratings[label] / total) ** 2 for label in ratings)
        if expected >= 1.0:
            return 1.0 if observed >= 1.0 else 0.0
        return round((observed - expected) / (1.0 - expected), 4)

    def score_agreement(pairs: list[tuple[float, float]]) -> dict[str, Any]:
        if not pairs:
            return {
                "pair_count": 0,
                "mean_absolute_difference": None,
                "within_one_rate": None,
                "quadratic_weighted_kappa": None,
            }
        absolute = [abs(a - b) for a, b in pairs]
        ratings = Counter(int(round(value)) for pair in pairs for value in pair)
        total = 2 * len(pairs)
        observed_disagreement = sum((a - b) ** 2 / 16 for a, b in pairs) / len(pairs)
        expected_disagreement = 0.0
        for a in range(1, 6):
            for b in range(1, 6):
                expected_disagreement += (
                    (ratings[a] / total)
                    * (ratings[b] / total)
                    * ((a - b) ** 2 / 16)
                )
        weighted_kappa = (
            1.0 - observed_disagreement / expected_disagreement
            if expected_disagreement > 0
            else 1.0 if observed_disagreement == 0 else 0.0
        )
        return {
            "pair_count": len(pairs),
            "mean_absolute_difference": round(sum(absolute) / len(absolute), 4),
            "within_one_rate": round(
                sum(value <= 1 for value in absolute) / len(absolute), 4
            ),
            "quadratic_weighted_kappa": round(weighted_kappa, 4),
        }

    all_dimension_pairs = [
        pair for pairs in dimension_pairs.values() for pair in pairs
    ]
    agreement = {
        "status": "available" if decision_pairs else "insufficient_data",
        "reviewer_count": len(reviewer_ids),
        "review_pair_count": len(decision_pairs),
        "decision_exact_agreement": (
            round(sum(a == b for a, b in decision_pairs) / len(decision_pairs), 4)
            if decision_pairs else None
        ),
        "decision_kappa": categorical_kappa(decision_pairs),
        "scores": score_agreement(all_dimension_pairs),
        "by_dimension": {
            dimension: score_agreement(pairs)
            for dimension, pairs in dimension_pairs.items()
        },
        "method": (
            "all unordered within-run pairs of distinct identified reviewers; "
            "decision Cohen-style kappa uses pooled symmetric marginals; score "
            "kappa uses quadratic 1-5 weights"
        ),
    }
    return {
        "schema_version": 2,
        "runs_scanned": len(rows),
        "reviewed_runs": reviewed_runs,
        "evaluation_records": evaluation_records,
        "multi_reviewed_runs": multi_reviewed_runs,
        "decision_disagreement_runs": decision_disagreement_runs,
        "average_score_spread": round(
            sum(score_spreads) / len(score_spreads), 2
        ) if score_spreads else 0.0,
        "latest_decisions": latest_decisions,
        "dimensions": dimensions,
        "agreement": agreement,
        "by_pipeline": finish_groups("pipeline"),
        "by_model": finish_groups("model"),
    }


def delete_run(run_id: str) -> bool:
    cur = execute("DELETE FROM agent_runs WHERE id=?", (run_id,))
    return bool(cur.rowcount)


def cancel_stale_runs() -> int:
    cur = execute(
        "UPDATE agent_runs SET status='cancelled', finished_at=?"
        " WHERE status IN ('pending','running')",
        (utc_now_iso(),),
    )
    return cur.rowcount or 0


# --------------------------------------------------------------------- steps


def create_step(
    run_id: str,
    *,
    agent: str,
    title: str = "",
    ordering: int = 0,
    model: str = "",
    prompt: str = "",
    meta: dict[str, Any] | None = None,
) -> str:
    sid = new_id("stp")
    execute(
        "INSERT INTO agent_steps (id, run_id, ordering, agent, title, status, model,"
        " prompt, meta, created_at) VALUES (?,?,?,?,?,'running',?,?,?,?)",
        (sid, run_id, ordering, agent, title, model, prompt, dumps(meta or {}),
         utc_now_iso()),
    )
    return sid


def finish_step(
    step_id: str,
    *,
    status: str = "done",
    output: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    duration_ms: int = 0,
    error: str = "",
    meta: dict[str, Any] | None = None,
) -> None:
    if meta is None:
        execute(
            "UPDATE agent_steps SET status=?, output=?, tokens_in=?, tokens_out=?,"
            " duration_ms=?, error=? WHERE id=?",
            (status, output, tokens_in, tokens_out, duration_ms, error, step_id),
        )
    else:
        execute(
            "UPDATE agent_steps SET status=?, output=?, tokens_in=?, tokens_out=?,"
            " duration_ms=?, error=?, meta=? WHERE id=?",
            (status, output, tokens_in, tokens_out, duration_ms, error,
             dumps(meta), step_id),
        )


def append_step_prompt(step_id: str, prompt: str) -> None:
    """Append one exact LLM call transcript to an agent step.

    A single logical step can issue several calls (the reader processes multiple
    papers concurrently), so replacing ``prompt`` would silently discard most of
    the audit trail. SQLite performs the concatenation atomically, which also
    prevents concurrent reader calls from losing one another.
    """
    text = str(prompt or "")
    if not step_id or not text:
        return
    separator = "\n\n===== NEXT LLM CALL =====\n\n"
    execute(
        "UPDATE agent_steps SET prompt=CASE WHEN prompt='' THEN ?"
        " ELSE prompt || ? || ? END WHERE id=?",
        (text, separator, text, step_id),
    )


def list_steps(run_id: str, *, include_prompt: bool = True) -> list[dict[str, Any]]:
    columns = (
        "id, run_id, ordering, agent, title, status, model, output, tokens_in,"
        " tokens_out, duration_ms, error, meta, created_at"
    )
    if include_prompt:
        columns += ", prompt"
    rows = query(
        f"SELECT {columns} FROM agent_steps WHERE run_id=? ORDER BY ordering, created_at",
        (run_id,),
    )
    out = []
    for row in rows:
        item = dict(row)
        item["meta"] = loads(item.get("meta"), {}) or {}
        out.append(item)
    return out


def get_step(step_id: str) -> dict[str, Any] | None:
    row = row_to_dict(query_one("SELECT * FROM agent_steps WHERE id=?", (step_id,)))
    if row is None:
        return None
    row["meta"] = loads(row.get("meta"), {}) or {}
    return row


def prune_step_prompts(keep_runs: int = 20) -> int:
    """Blank prompt text on older runs to keep the database small.

    Outputs, token counts and errors are preserved - only the (large, mostly
    reconstructable) prompt bodies go.
    """
    row = query_one(
        "SELECT created_at FROM agent_runs ORDER BY created_at DESC LIMIT 1 OFFSET ?",
        (keep_runs,),
    )
    if row is None:
        return 0
    cur = execute(
        "UPDATE agent_steps SET prompt='[pruned]' WHERE prompt != '[pruned]'"
        " AND run_id IN (SELECT id FROM agent_runs WHERE created_at <= ?)",
        (row["created_at"],),
    )
    return cur.rowcount or 0


# ----------------------------------------------------------------- llm usage


def record_usage(
    *,
    provider: str,
    model: str,
    purpose: str = "",
    run_id: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    duration_ms: int = 0,
    ok: bool = True,
) -> str:
    usage_id = new_id("use")
    execute(
        "INSERT INTO llm_usage (id, created_at, provider, model, purpose, run_id,"
        " tokens_in, tokens_out, cost_usd, duration_ms, ok)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (usage_id, utc_now_iso(), provider, model, purpose, run_id or None,
         int(tokens_in), int(tokens_out), float(cost_usd), int(duration_ms),
         int(ok)),
    )
    return usage_id


def mark_usage_failed(usage_id: str) -> None:
    """Reclassify a transport-successful call whose output contract failed.

    JSON parsing happens after a provider has returned and been accounted for.
    Updating that same row keeps the ledger at exactly one row per network call
    while ensuring invalid structured output counts as a failed call.
    """
    if usage_id:
        execute("UPDATE llm_usage SET ok=0 WHERE id=?", (usage_id,))


def usage_summary(days: int = 30) -> dict[str, Any]:
    """Totals and per-model breakdown for the usage panel."""
    total = query_one(
        "SELECT COUNT(*) AS calls, COALESCE(SUM(tokens_in),0) AS tin,"
        " COALESCE(SUM(tokens_out),0) AS tout, COALESCE(SUM(cost_usd),0) AS cost,"
        " COALESCE(SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END),0) AS failures"
        " FROM llm_usage WHERE created_at >= datetime('now', ?)",
        (f"-{int(days)} days",),
    )
    by_model = query(
        "SELECT provider, model, COUNT(*) AS calls,"
        " COALESCE(SUM(tokens_in),0) AS tin, COALESCE(SUM(tokens_out),0) AS tout,"
        " COALESCE(SUM(cost_usd),0) AS cost FROM llm_usage"
        " WHERE created_at >= datetime('now', ?)"
        " GROUP BY provider, model ORDER BY cost DESC, calls DESC",
        (f"-{int(days)} days",),
    )
    by_day = query(
        "SELECT substr(created_at,1,10) AS day, COUNT(*) AS calls,"
        " COALESCE(SUM(cost_usd),0) AS cost FROM llm_usage"
        " WHERE created_at >= datetime('now', ?) GROUP BY day ORDER BY day",
        (f"-{int(days)} days",),
    )
    return {
        "window_days": days,
        "totals": dict(total) if total else {},
        "by_model": [dict(r) for r in by_model],
        "by_day": [dict(r) for r in by_day],
    }

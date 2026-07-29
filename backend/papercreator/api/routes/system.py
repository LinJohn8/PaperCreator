"""System routes: health, paths, capabilities, jobs, event stream, maintenance.

``/api/system/health`` is the single call the frontend makes on startup to decide
what to show. It reports every subsystem's real state - which providers work,
whether an LLM is configured, which optional analysis packages are present - so
the UI can disable features with an explanation instead of failing when clicked.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ... import __version__
from ...core import db, events, jobs
from ...core.config import dotenv_source, get_settings
from ...core.errors import NotFoundError
from ...core.logging_setup import get_logger
from ...core.paths import get_paths

log = get_logger(__name__)
router = APIRouter(prefix="/api/system", tags=["system"])


@router.post("/shutdown", include_in_schema=False)
def desktop_shutdown(
    request: Request,
    x_papercreator_shutdown: str = Header(default=""),
) -> dict[str, bool]:
    """Gracefully stop only the backend owned by this desktop process.

    The route is deliberately absent from OpenAPI and behaves as not found
    unless Electron supplied a fresh 256-bit per-launch capability through the
    child environment.  It is not a renderer or public automation API.
    """
    expected = os.environ.get("PC_DESKTOP_SHUTDOWN_TOKEN", "")
    callback = getattr(request.app.state, "request_shutdown", None)
    if (
        not expected
        or not x_papercreator_shutdown
        or not hmac.compare_digest(expected, x_papercreator_shutdown)
        or not callable(callback)
    ):
        raise HTTPException(status_code=404, detail="not found")
    callback()
    return {"ok": True}


@router.get("/health")
def health() -> dict[str, Any]:
    """Full capability report. Cheap enough to poll; no network calls."""
    from ...analysis import pipeline as analysis_pipeline
    from ...convert import exporters
    from ...llm import registry as llm_registry
    from ...retrieval import registry as retrieval_registry
    from ...vcs import git as git_module
    # Import the function from the module directly.  ``papercreator.api`` also
    # exports a FastAPI instance named ``app``; importing ``from .. import app``
    # therefore resolves to that instance rather than the module.
    from ..app import uptime_seconds

    providers = retrieval_registry.describe_all()
    settings = get_settings()
    return {
        "ok": True,
        "version": __version__,
        "uptime_s": uptime_seconds(),
        "paths": get_paths().describe(),
        "dotenv": dotenv_source(),
        "database": db.stats(),
        "jobs": {
            "active": jobs.manager.active_count(),
            "recent": len(jobs.manager.list(limit=10)),
        },
        "events": {"subscribers": events.bus.subscriber_count},
        "retrieval": {
            "total": len(providers),
            "available": [p["id"] for p in providers if p["available"]],
            "unavailable": {
                p["id"]: p["unavailable_reason"]
                for p in providers if not p["available"]
            },
            "enabled": settings.retrieval.enabled_providers,
        },
        "llm": llm_registry.status(),
        "analysis": analysis_pipeline.describe_capabilities(),
        "export": exporters.describe_capabilities(),
        "git": {
            "available": git_module.git_available(),
            "auto_commit": settings.writing.auto_git_commit,
        },
        # The renderer reads the persisted locale during its first health
        # request.  Keep UI preferences in this cheap startup contract.
        "ui": settings.ui.model_dump(),
        "identity_configured": bool(settings.identity.contact_email),
    }


@router.get("/paths")
def paths() -> dict[str, Any]:
    return get_paths().describe()


@router.get("/capabilities")
def capabilities() -> dict[str, Any]:
    """Everything the UI needs to build its option lists in one call."""
    from ...agents import orchestrator, roles
    from ...analysis import pipeline as analysis_pipeline
    from ...convert import exporters
    from ...retrieval import registry as retrieval_registry
    from ...writing import templates

    return {
        "retrieval_providers": retrieval_registry.describe_all(),
        "analysis": analysis_pipeline.describe_capabilities(),
        "agent_pipelines": orchestrator.describe_pipelines(),
        "agent_roles": roles.describe_roles(),
        "templates": templates.list_templates(),
        "export": exporters.describe_capabilities(),
    }


@router.get("/events")
async def event_stream(after: int = Query(0, description="last seen event seq")):
    """Server-sent events: job progress, agent deltas, document updates.

    ``after`` replays buffered events so a reconnecting client misses nothing.
    Buffering is bounded, so a very long disconnect may still lose events - the
    client refetches state on reconnect for that reason.
    """
    return StreamingResponse(
        events.sse_stream(after_seq=after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Disable proxy buffering, which would defeat streaming entirely.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/jobs")
def list_jobs(
    project_id: str = "", status: str = "", limit: int = Query(50, le=200)
) -> dict[str, Any]:
    return {"items": jobs.manager.list(project_id=project_id, status=status, limit=limit)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = jobs.manager.get(job_id)
    if job is None:
        raise NotFoundError(f"job {job_id} not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    """Request cancellation. Cooperative: workers stop at their next checkpoint."""
    cancelled = jobs.manager.cancel(job_id)
    return {
        "requested": cancelled,
        "job_id": job_id,
        "note": "cancellation is cooperative; the job stops at its next checkpoint",
    }


class LogQuery(BaseModel):
    lines: int = 200
    which: str = "main"


@router.get("/logs")
def read_logs(
    lines: int = Query(200, le=5000), which: str = Query("main", pattern="^(main|errors)$")
) -> dict[str, Any]:
    """Tail a log file, for the app's Output panel.

    Secrets are already scrubbed at write time by the logging filter, so this is
    safe to display.
    """
    path = get_paths().logs_dir / (
        "papercreator.log" if which == "main" else "errors.log"
    )
    if not path.is_file():
        return {"path": str(path), "lines": [], "exists": False}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            content = handle.readlines()
    except OSError as exc:
        return {"path": str(path), "lines": [], "exists": True, "error": str(exc)}
    return {
        "path": str(path),
        "exists": True,
        "total_lines": len(content),
        "lines": [line.rstrip("\n") for line in content[-lines:]],
    }


class MaintenanceRequest(BaseModel):
    vacuum: bool = False
    prune_jobs: bool = True
    prune_prompts: bool = False
    clear_http_cache: bool = False
    clear_embeddings: str = ""       # "" | model name | "all"


@router.post("/maintenance")
def maintenance(request: MaintenanceRequest) -> dict[str, Any]:
    """Housekeeping. Each action is opt-in and reports what it did.

    Nothing here touches manuscripts or the paper library - only regenerable
    caches and bookkeeping rows.
    """
    from ...retrieval.http_client import HttpClient
    from ...store import analyses as analyses_store
    from ...store import runs as runs_store

    result: dict[str, Any] = {}
    if request.prune_jobs:
        result["jobs_pruned"] = jobs.manager.prune()
    if request.prune_prompts:
        result["prompts_pruned"] = runs_store.prune_step_prompts()
    if request.clear_http_cache:
        client = HttpClient()
        result["http_cache_files_removed"] = client.cache.clear()
    if request.clear_embeddings:
        model = "" if request.clear_embeddings == "all" else request.clear_embeddings
        result["embeddings_removed"] = analyses_store.clear_embeddings(model)
    if request.vacuum:
        db.vacuum()
        result["vacuumed"] = True
    result["database"] = db.stats()
    log.info("maintenance run: %s", {k: v for k, v in result.items() if k != "database"})
    return result


@router.get("/cache")
def cache_stats() -> dict[str, Any]:
    from ...retrieval.http_client import HttpClient
    from ...store import analyses as analyses_store

    return {
        "http": HttpClient().cache.stats(),
        "embeddings": analyses_store.embedding_stats(),
        "paths": {
            "http": str(get_paths().http_cache_dir),
            "models": str(get_paths().models_dir),
        },
    }


@router.get("/usage")
def usage(days: int = Query(30, le=365)) -> dict[str, Any]:
    """LLM token and cost ledger."""
    from ...store import runs as runs_store

    return runs_store.usage_summary(days=days)

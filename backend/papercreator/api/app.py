"""FastAPI application factory.

Startup order matters and is enforced here:

1. ``.env`` -> process environment (before any settings read)
2. paths resolved and created
3. logging installed (so everything after this is captured)
4. settings assembled
5. database created/migrated, orphaned jobs reconciled
6. skill registry synced from the filesystem
7. routes registered
8. static frontend mounted, if a build exists

Failures in steps 6-8 are logged and tolerated: a broken skill folder or a
missing frontend build must not stop the API from serving.

CORS: the Electron renderer loads over ``file://`` (origin ``null``) in
production and ``http://localhost:5173`` in dev. Both are allowed, plus anything
the user adds via ``PC_CORS_EXTRA``. The server binds to 127.0.0.1 by default, so
this is not a public surface.
"""

from __future__ import annotations

import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..core import db, events, jobs
from ..core.config import get_settings, load_dotenv_file
from ..core.errors import AppError
from ..core.logging_setup import get_logger, setup_logging
from ..core.paths import find_repo_root, get_paths

log = get_logger(__name__)

_started_at = time.time()


def _frontend_dir() -> Path | None:
    """Locate a built frontend, if one exists.

    Two locations: the packaged app puts it next to the backend; a repo checkout
    has it under ``apps/desktop/dist``. Returning ``None`` is normal in
    development, where Vite serves the UI on its own port.
    """
    candidates: list[Path] = [Path(__file__).resolve().parent.parent / "web"]
    repo = find_repo_root()
    if repo is not None:
        candidates.append(repo / "apps" / "desktop" / "dist")
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown."""
    settings = get_settings()
    paths = get_paths()
    log.info("=" * 68)
    log.info("PaperCreator backend %s starting", __version__)
    for key, value in paths.describe().items():
        log.info("  %-9s %s", key, value)

    db.init_db()
    stats = db.stats()
    log.info(
        "database schema v%s, %s papers, %s projects",
        stats["schema_version"], stats["counts"].get("papers"),
        stats["counts"].get("projects"),
    )

    # A graceful failure/cancellation removes its own staging tree.  Only an
    # abrupt process/OS stop can leave the reserved `.partial-res_*` entries,
    # and DB initialisation has just marked every old running job failed, so no
    # live importer can own them at this point.
    try:
        from ..store import resources as resources_store

        cleanup = resources_store.cleanup_stale_partials()
        if cleanup["failed"]:
            log.warning(
                "resource import staging cleanup needs attention: %s",
                cleanup["failed"],
            )
    except Exception as exc:  # noqa: BLE001 - stale cleanup must not block startup
        log.warning("stale resource import staging cleanup failed: %s", exc)

    try:
        from ..skills import loader as skills_loader

        summary = skills_loader.sync_registry()
        log.info("skills: %s available %s", summary["count"], summary["by_scope"])
    except Exception as exc:  # noqa: BLE001 - a bad skill must not block startup
        log.warning("skill registry sync failed: %s", exc)

    from ..llm import registry as llm_registry

    llm_status = llm_registry.status()
    if llm_status["has_any"]:
        log.info("LLM providers usable: %s", ", ".join(llm_status["usable"]))
    else:
        log.warning(
            "no LLM provider configured - agent features will refuse to run until "
            "an API key is set in Settings > Models (or Ollama is running)"
        )

    from ..retrieval import registry as retrieval_registry

    available = [p["id"] for p in retrieval_registry.describe_all() if p["available"]]
    log.info("retrieval providers available: %s", ", ".join(available))
    bind_host = getattr(app.state, "bind_host", settings.server.host)
    bind_port = getattr(app.state, "bind_port", settings.server.port)
    log.info("listening on http://%s:%s", bind_host, bind_port)
    log.info("=" * 68)

    yield

    log.info("shutting down: cancelling %s active job(s)", jobs.manager.active_count())
    jobs.manager.shutdown(wait=False)
    try:
        checkpoint = db.checkpoint_wal()
        if checkpoint["busy"]:
            log.warning("SQLite WAL checkpoint remained busy at shutdown: %s", checkpoint)
        else:
            log.info("SQLite WAL checkpoint complete: %s", checkpoint)
    except Exception as exc:  # noqa: BLE001 - shutdown must still close the handle
        log.warning("SQLite WAL checkpoint failed during shutdown: %s", exc)
    db.close_connection()


def create_app(*, bind_host: str | None = None, bind_port: int | None = None) -> FastAPI:
    load_dotenv_file()
    paths = get_paths().ensure()
    settings = get_settings()
    setup_logging(settings.server.log_level, paths.logs_dir)

    # Must happen before any subsystem import can pull in huggingface_hub, which
    # freezes its endpoint at import time. See analysis.embeddings for why.
    from ..analysis import embeddings as _embeddings

    environment = _embeddings.model_environment()
    log.info("model endpoint: %s", environment["endpoint"])

    app = FastAPI(
        title="PaperCreator API",
        version=__version__,
        description=(
            "Local backend for the PaperCreator workbench: scholarly retrieval, "
            "landscape analysis, multi-agent writing, export and versioning."
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.bind_host = bind_host or settings.server.host
    app.state.bind_port = bind_port if bind_port is not None else settings.server.port

    origins = [
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:4173", "http://127.0.0.1:4173",
        f"http://{app.state.bind_host}:{app.state.bind_port}",
        *settings.server.cors_extra,
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        # The Electron renderer sends Origin: null from a file:// page.
        allow_origin_regex=r"^(file://|null|app://).*$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_error_handlers(app)
    _register_middleware(app)
    _register_routes(app)
    _mount_frontend(app)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        # Expected, actionable failures: the message is written for the user, so
        # it is returned verbatim without a traceback.
        log.info("%s: %s", exc.code, exc.message)
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        # Anything reaching here is a bug. Log the traceback for the developer,
        # return something honest but non-leaking to the client.
        log.error(
            "unhandled error on %s %s: %s",
            request.method, request.url.path, exc,
        )
        log.debug("traceback:\n%s", traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": (
                        f"{type(exc).__name__}: {exc}. This is a bug - see "
                        f"logs/errors.log for the traceback."
                    ),
                    "details": {"path": str(request.url.path)},
                }
            },
        )


def _register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_log(request: Request, call_next: Any) -> Any:
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000
        # Only log the interesting ones: SSE never completes, and health polling
        # would drown the log.
        if request.url.path.startswith("/api") and not request.url.path.endswith(
            ("/events", "/health")
        ):
            level = log.warning if response.status_code >= 400 else log.debug
            level(
                "%s %s -> %s (%.0fms)",
                request.method, request.url.path, response.status_code, duration_ms,
            )
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.0f}"
        return response


def _register_routes(app: FastAPI) -> None:
    from .routes import (
        agents,
        analysis,
        assistant,
        export,
        library,
        projects,
        prompts,
        search,
        settings,
        skills,
        system,
        versions,
        workbench,
        writing,
    )

    app.include_router(system.router)
    app.include_router(settings.router)
    app.include_router(projects.router)
    app.include_router(search.router)
    app.include_router(library.router)
    app.include_router(analysis.router)
    app.include_router(assistant.router)
    app.include_router(writing.router)
    app.include_router(agents.router)
    app.include_router(skills.router)
    app.include_router(prompts.router)
    app.include_router(export.router)
    app.include_router(versions.router)
    app.include_router(workbench.router)


def _mount_frontend(app: FastAPI) -> None:
    directory = _frontend_dir()
    if directory is None:
        log.info(
            "no built frontend found; run `npm run dev` for the Vite dev server "
            "or `npm run build` to bundle it"
        )
        return
    # html=True makes StaticFiles serve index.html for unknown paths, which is
    # what a client-side-routed SPA needs.
    app.mount("/", StaticFiles(directory=str(directory), html=True), name="frontend")
    log.info("serving frontend from %s", directory)


def uptime_seconds() -> float:
    return round(time.time() - _started_at, 1)


app = create_app()

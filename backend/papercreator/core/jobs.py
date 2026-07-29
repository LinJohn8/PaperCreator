"""Background job runner.

Search, analysis, agent runs and exports all take longer than an HTTP request
should. They run here: the API enqueues a job, returns its id immediately, and
the UI follows progress over SSE.

Why threads and not asyncio tasks: the heavy work is CPU-bound and synchronous
(numpy, UMAP, HDBSCAN, sqlite, subprocess git). A thread pool keeps the event
loop responsive without forcing every subsystem to be async.

Cancellation is cooperative. There is no way to kill a thread safely in
CPython, so :meth:`JobHandle.cancel` sets a flag and worker code must call
``ctx.raise_if_cancelled()`` between units of work. Every long loop in this
codebase does; if you add one, do the same or the UI's cancel button lies.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from . import events
from .db import dumps, execute, loads, query, query_one, row_to_dict
from .errors import CancelledError, NotFoundError, error_diagnostics
from .logging_setup import get_logger
from .util import utc_now_iso

log = get_logger(__name__)

_MAX_WORKERS = 4


class JobCancelled(Exception):
    """Internal signal raised by :meth:`JobContext.raise_if_cancelled`."""


@dataclass
class JobContext:
    """Handed to every worker function. The only channel a worker needs.

    Workers should:

    * call :meth:`progress` with a 0..1 fraction and a human message,
    * call :meth:`raise_if_cancelled` between chunks of work,
    * return a JSON-serialisable dict as the result.
    """

    job_id: str
    kind: str
    project_id: str | None
    payload: dict[str, Any]
    _cancel: threading.Event = field(default_factory=threading.Event)
    _last_progress: float = 0.0

    # ------------------------------------------------------------ progress
    def progress(self, fraction: float, message: str = "", **extra: Any) -> None:
        value = max(0.0, min(1.0, float(fraction)))
        self._last_progress = value
        execute(
            "UPDATE jobs SET progress=?, message=? WHERE id=?",
            (value, message, self.job_id),
        )
        events.publish(
            events.JOB_PROGRESS,
            {"kind": self.kind, "progress": value, "message": message, **extra},
            project_id=self.project_id,
            job_id=self.job_id,
        )

    def log(self, message: str, **extra: Any) -> None:
        """Progress message without moving the bar."""
        log.info("[job %s] %s", self.job_id[:8], message)
        events.publish(
            events.JOB_PROGRESS,
            {"kind": self.kind, "progress": self._last_progress,
             "message": message, **extra},
            project_id=self.project_id,
            job_id=self.job_id,
        )

    # -------------------------------------------------------- cancellation
    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def raise_if_cancelled(self) -> None:
        if self._cancel.is_set():
            raise JobCancelled()

    def request_cancel(self) -> None:
        self._cancel.set()


class JobHandle:
    def __init__(self, context: JobContext) -> None:
        self.context = context
        self.done = threading.Event()

    @property
    def id(self) -> str:
        return self.context.job_id

    def cancel(self) -> None:
        self.context.request_cancel()


class JobManager:
    """Owns the thread pool and the in-memory handle table.

    Job *rows* live in SQLite (survive restart, queryable); job *handles* live
    here (cancellation, wait). A handle disappears on process exit, which is
    why ``init_db`` marks leftover running rows as failed.
    """

    def __init__(self, max_workers: int = _MAX_WORKERS) -> None:
        self._max_workers = max_workers
        self._executor = self._new_executor()
        self._shutdown = False
        self._handles: dict[str, JobHandle] = {}
        self._lock = threading.RLock()

    def _new_executor(self) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="pc-job"
        )

    def _ensure_executor(self) -> ThreadPoolExecutor:
        """Return a live pool, recreating it after an application restart.

        FastAPI lifespan shutdown is not necessarily process shutdown: tests,
        embedded launchers and reloaders may start a fresh app in the same
        interpreter.  ``ThreadPoolExecutor`` cannot be restarted once closed.
        """
        with self._lock:
            if self._shutdown:
                self._executor = self._new_executor()
                self._shutdown = False
            return self._executor

    # ------------------------------------------------------------ submit
    def submit(
        self,
        kind: str,
        func: Callable[[JobContext], Any],
        *,
        payload: dict[str, Any] | None = None,
        project_id: str | None = None,
        job_id: str | None = None,
    ) -> JobHandle:
        jid = job_id or uuid.uuid4().hex
        context = JobContext(
            job_id=jid, kind=kind, project_id=project_id, payload=payload or {}
        )
        handle = JobHandle(context)
        execute(
            "INSERT INTO jobs (id, kind, project_id, status, progress, message,"
            " payload, created_at) VALUES (?,?,?,'queued',0,'',?,?)",
            (jid, kind, project_id, dumps(payload or {}), utc_now_iso()),
        )
        with self._lock:
            # Scheduling and shutdown share the same lock.  Without this, a
            # shutdown could close the pool in the small window between
            # _ensure_executor() and submit(), leaving a queued DB row that can
            # never run.
            executor = self._ensure_executor()
            self._handles[jid] = handle
            events.publish(
                events.JOB_CREATED, {"kind": kind}, project_id=project_id, job_id=jid
            )
            executor.submit(self._run, handle, func)
        return handle

    def _run(self, handle: JobHandle, func: Callable[[JobContext], Any]) -> None:
        ctx = handle.context
        started = time.perf_counter()
        execute(
            "UPDATE jobs SET status='running', started_at=? WHERE id=?",
            (utc_now_iso(), ctx.job_id),
        )
        try:
            result = func(ctx)
            payload = result if isinstance(result, dict) else {"value": result}
            execute(
                "UPDATE jobs SET status='done', progress=1, result=?, finished_at=?"
                " WHERE id=?",
                (dumps(payload), utc_now_iso(), ctx.job_id),
            )
            events.publish(
                events.JOB_DONE,
                {"kind": ctx.kind, "result": payload,
                 "durationMs": int((time.perf_counter() - started) * 1000)},
                project_id=ctx.project_id, job_id=ctx.job_id,
            )
        except JobCancelled:
            execute(
                "UPDATE jobs SET status='cancelled', error='cancelled by user',"
                " finished_at=? WHERE id=?",
                (utc_now_iso(), ctx.job_id),
            )
            events.publish(
                events.JOB_FAILED, {"kind": ctx.kind, "error": "cancelled",
                                    "cancelled": True},
                project_id=ctx.project_id, job_id=ctx.job_id,
            )
        except Exception as exc:  # noqa: BLE001 - boundary: must not kill the pool
            detail = f"{type(exc).__name__}: {exc}"
            failure = error_diagnostics(exc)
            log.error("job %s (%s) failed: %s", ctx.job_id[:8], ctx.kind, detail)
            log.debug("traceback:\n%s", traceback.format_exc())
            execute(
                "UPDATE jobs SET status='failed', error=?, result=?, finished_at=?"
                " WHERE id=?",
                (detail, dumps({"failure": failure}), utc_now_iso(), ctx.job_id),
            )
            events.publish(
                events.JOB_FAILED, {"kind": ctx.kind, "error": detail, **failure},
                project_id=ctx.project_id, job_id=ctx.job_id,
            )
        finally:
            handle.done.set()

    # ------------------------------------------------------------ control
    def cancel(self, job_id: str) -> bool:
        with self._lock:
            handle = self._handles.get(job_id)
        if handle is None:
            # Not in this process (restarted) - only the row can be updated.
            row = query_one("SELECT status FROM jobs WHERE id=?", (job_id,))
            if row is None:
                raise NotFoundError(f"job {job_id} not found")
            if row["status"] in ("queued", "running"):
                execute(
                    "UPDATE jobs SET status='cancelled', error='cancelled (no live"
                    " handle)', finished_at=? WHERE id=?",
                    (utc_now_iso(), job_id),
                )
                return True
            return False
        handle.cancel()
        return True

    def wait(self, job_id: str, timeout: float | None = None) -> dict[str, Any]:
        """Block until the job finishes. Used by tests and sync API variants."""
        with self._lock:
            handle = self._handles.get(job_id)
        if handle is not None:
            handle.done.wait(timeout)
        row = self.get(job_id)
        if row is None:
            raise NotFoundError(f"job {job_id} not found")
        return row

    def result_or_raise(self, job_id: str, timeout: float | None = None) -> dict[str, Any]:
        row = self.wait(job_id, timeout)
        if row["status"] == "failed":
            raise RuntimeError(row.get("error") or "job failed")
        if row["status"] == "cancelled":
            raise CancelledError("job cancelled")
        return row.get("result") or {}

    # ------------------------------------------------------------ queries
    def get(self, job_id: str) -> dict[str, Any] | None:
        row = row_to_dict(query_one("SELECT * FROM jobs WHERE id=?", (job_id,)))
        if row is None:
            return None
        row["payload"] = loads(row.get("payload"), {})
        row["result"] = loads(row.get("result"), {})
        return row

    def list(
        self, *, project_id: str | None = None, status: str | None = None, limit: int = 50
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
            f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        out = []
        for row in rows:
            item = dict(row)
            item["payload"] = loads(item.get("payload"), {})
            item["result"] = loads(item.get("result"), {})
            out.append(item)
        return out

    def active_count(self) -> int:
        row = query_one(
            "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running')"
        )
        return int(row["n"]) if row else 0

    def shutdown(self, wait: bool = False) -> None:
        """Ask in-flight jobs to stop, then tear down the pool."""
        with self._lock:
            handles = list(self._handles.values())
            if self._shutdown:
                return
            executor = self._executor
            self._shutdown = True
        for handle in handles:
            handle.cancel()
        executor.shutdown(wait=wait, cancel_futures=True)

    def prune(self, keep: int = 200) -> int:
        """Drop old finished job rows so the table stays small."""
        row = query_one(
            "SELECT created_at FROM jobs WHERE status NOT IN ('queued','running')"
            " ORDER BY created_at DESC LIMIT 1 OFFSET ?",
            (keep,),
        )
        if row is None:
            return 0
        cur = execute(
            "DELETE FROM jobs WHERE status NOT IN ('queued','running')"
            " AND created_at <= ?",
            (row["created_at"],),
        )
        return cur.rowcount or 0


manager = JobManager()

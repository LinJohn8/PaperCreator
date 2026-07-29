"""In-process event bus, bridged to the browser over Server-Sent Events.

Producers (jobs, agents, retrieval) call :func:`publish` from *any* thread.
Each SSE connection owns a :class:`Subscriber` with a bounded queue.

Design choices worth knowing before changing this:

* **Thread-safe publish, async consume.** ``publish`` is a plain function so
  synchronous worker threads can emit without an event loop. Delivery into each
  subscriber's ``asyncio.Queue`` goes through ``loop.call_soon_threadsafe``.
* **Bounded queues, drop-oldest.** A slow or paused browser tab must never
  apply backpressure to an agent run. When a queue is full the oldest event is
  discarded and a ``dropped`` counter increments, reported in the next event.
* **Replay buffer.** The last :data:`_RING_SIZE` events are retained so a
  reconnecting client can pass ``?after=<seq>`` and not miss progress across a
  dropped connection.
* SSE over WebSocket because traffic is strictly server->client, it survives
  proxies, and the browser reconnects on its own.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .db import dumps
from .logging_setup import get_logger

log = get_logger(__name__)

_RING_SIZE = 512
_QUEUE_SIZE = 256


@dataclass
class Event:
    seq: int
    type: str
    payload: dict[str, Any]
    ts: float = field(default_factory=time.time)
    # Optional routing keys so a client can filter client-side.
    project_id: str | None = None
    job_id: str | None = None

    def to_sse(self) -> str:
        """Serialise to the SSE wire format (id/event/data)."""
        body = dumps({
            "seq": self.seq,
            "type": self.type,
            "ts": self.ts,
            "projectId": self.project_id,
            "jobId": self.job_id,
            "payload": self.payload,
        })
        return f"id: {self.seq}\nevent: {self.type}\ndata: {body}\n\n"


class Subscriber:
    """One SSE connection. Created by :meth:`EventBus.subscribe`."""

    def __init__(self, bus: "EventBus", loop: asyncio.AbstractEventLoop) -> None:
        self._bus = bus
        self._loop = loop
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        self.dropped = 0

    def offer(self, event: Event) -> None:
        """Called from the publisher's thread. Never blocks, never raises."""
        try:
            self._loop.call_soon_threadsafe(self._put, event)
        except RuntimeError:
            # Loop already closed - the connection is gone; the bus removes
            # this subscriber on the next publish pass.
            pass

    def _put(self, event: Event) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
                self.dropped += 1
            except asyncio.QueueEmpty:
                pass
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1

    def close(self) -> None:
        self._bus.unsubscribe(self)


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: list[Subscriber] = []
        self._ring: deque[Event] = deque(maxlen=_RING_SIZE)
        self._counter = itertools.count(1)

    # ----------------------------------------------------------- producing
    def publish(
        self,
        type_: str,
        payload: dict[str, Any] | None = None,
        *,
        project_id: str | None = None,
        job_id: str | None = None,
    ) -> Event:
        event = Event(
            seq=next(self._counter),
            type=type_,
            payload=payload or {},
            project_id=project_id,
            job_id=job_id,
        )
        with self._lock:
            self._ring.append(event)
            targets = list(self._subscribers)
        for subscriber in targets:
            subscriber.offer(event)
        return event

    # ----------------------------------------------------------- consuming
    def subscribe(self, loop: asyncio.AbstractEventLoop | None = None) -> Subscriber:
        subscriber = Subscriber(self, loop or asyncio.get_running_loop())
        with self._lock:
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def replay(self, after_seq: int) -> list[Event]:
        """Buffered events newer than ``after_seq`` (oldest first)."""
        with self._lock:
            return [e for e in self._ring if e.seq > after_seq]

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


bus = EventBus()


def publish(
    type_: str,
    payload: dict[str, Any] | None = None,
    *,
    project_id: str | None = None,
    job_id: str | None = None,
) -> Event:
    """Module-level shortcut for the process-wide bus."""
    return bus.publish(type_, payload, project_id=project_id, job_id=job_id)


async def sse_stream(
    after_seq: int = 0, heartbeat_s: float = 20.0
) -> AsyncIterator[str]:
    """Async generator of SSE frames for ``StreamingResponse``.

    Emits a ``: heartbeat`` comment when idle so intermediaries and the client's
    own timeout logic can tell a live-but-quiet stream from a dead one.
    """
    subscriber = bus.subscribe()
    try:
        for event in bus.replay(after_seq):
            yield event.to_sse()
        yield ": connected\n\n"
        while True:
            try:
                event = await asyncio.wait_for(subscriber.queue.get(), timeout=heartbeat_s)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            yield event.to_sse()
    except asyncio.CancelledError:
        raise
    finally:
        if subscriber.dropped:
            log.warning("SSE subscriber dropped %s event(s)", subscriber.dropped)
        subscriber.close()


# Event type constants. Keep in sync with apps/desktop/src/api/events.ts.
JOB_CREATED = "job.created"
JOB_PROGRESS = "job.progress"
JOB_DONE = "job.done"
JOB_FAILED = "job.failed"
SEARCH_PROVIDER = "search.provider"
SEARCH_DONE = "search.done"
ANALYSIS_PROGRESS = "analysis.progress"
ANALYSIS_DONE = "analysis.done"
AGENT_RUN_STARTED = "agent.run.started"
AGENT_STEP_STARTED = "agent.step.started"
AGENT_STEP_DELTA = "agent.step.delta"
AGENT_STEP_DONE = "agent.step.done"
AGENT_RUN_DONE = "agent.run.done"
AGENT_RUN_FAILED = "agent.run.failed"
DOCUMENT_UPDATED = "document.updated"
PROJECT_UPDATED = "project.updated"
LIBRARY_UPDATED = "library.updated"
SKILL_UPDATED = "skill.updated"
NOTIFY = "notify"

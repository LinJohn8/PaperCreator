/**
 * Server-sent event stream.
 *
 * One `EventSource` for the whole app, multiplexed to subscribers by event type.
 * Opening one per view would multiply connections and each would replay the
 * backend's buffer independently.
 *
 * Reconnection: `EventSource` retries on its own, but it does not know what was
 * missed. The last sequence number is tracked and passed as `?after=`, which the
 * backend replays from its ring buffer - so progress is not lost across a brief
 * disconnect. Beyond the buffer's depth events are genuinely gone, which is why
 * views also refetch state when the connection is re-established.
 */

import { api, resolveBaseUrl } from "./client";
import type { JobRecord } from "./types";

export type EventType =
  | "job.created"
  | "job.progress"
  | "job.done"
  | "job.failed"
  | "search.provider"
  | "search.done"
  | "analysis.progress"
  | "analysis.done"
  | "agent.run.started"
  | "agent.step.started"
  | "agent.step.delta"
  | "agent.step.done"
  | "agent.run.done"
  | "agent.run.failed"
  | "document.updated"
  | "project.updated"
  | "library.updated"
  | "skill.updated"
  | "notify";

export interface AppEvent<P = Record<string, unknown>> {
  seq: number;
  type: EventType;
  ts: number;
  projectId: string | null;
  jobId: string | null;
  payload: P;
}

type Handler = (event: AppEvent) => void;
type ConnectionHandler = (state: ConnectionState) => void;
export type ConnectionState = "connecting" | "open" | "closed";

export class JobFailureError extends Error {
  readonly failure: Record<string, unknown>;

  constructor(message: string, failure: Record<string, unknown>) {
    super(message);
    this.name = "JobFailureError";
    this.failure = failure;
  }
}

const handlers = new Map<string, Set<Handler>>();
const connectionHandlers = new Set<ConnectionHandler>();

let source: EventSource | null = null;
let lastSeq = 0;
let state: ConnectionState = "closed";
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let reconnectDelay = 1000;

function setState(next: ConnectionState): void {
  if (state === next) return;
  state = next;
  connectionHandlers.forEach((handler) => handler(next));
}

function dispatch(event: AppEvent): void {
  if (event.seq > lastSeq) lastSeq = event.seq;
  for (const key of [event.type, "*"]) {
    const set = handlers.get(key);
    if (!set) continue;
    for (const handler of set) {
      try {
        handler(event);
      } catch (error) {
        // One bad subscriber must not stop delivery to the others.
        console.error(`event handler for ${event.type} threw`, error);
      }
    }
  }
}

export function connect(): void {
  if (source) return;
  setState("connecting");
  const url = `${resolveBaseUrl()}/api/system/events?after=${lastSeq}`;
  source = new EventSource(url);

  source.onopen = () => {
    setState("open");
    reconnectDelay = 1000;
  };

  // The backend names each event, but a named listener per type would need
  // registering up front. `onmessage` catches unnamed frames; named ones need
  // explicit listeners, so both paths are wired.
  source.onmessage = (message) => handleRaw(message.data);
  const named: EventType[] = [
    "job.created", "job.progress", "job.done", "job.failed",
    "search.provider", "search.done",
    "analysis.progress", "analysis.done",
    "agent.run.started", "agent.step.started", "agent.step.delta",
    "agent.step.done", "agent.run.done", "agent.run.failed",
    "document.updated", "project.updated", "library.updated",
    "skill.updated", "notify",
  ];
  for (const type of named) {
    source.addEventListener(type, (message) =>
      handleRaw((message as MessageEvent).data),
    );
  }

  source.onerror = () => {
    // EventSource reconnects itself, but a closed connection needs a manual
    // reopen with the updated `after` cursor - otherwise the replay would start
    // from the original position again.
    setState("connecting");
    if (source && source.readyState === EventSource.CLOSED) {
      source.close();
      source = null;
      scheduleReconnect();
    }
  };
}

function handleRaw(data: string): void {
  if (!data || data.startsWith(":")) return;
  try {
    dispatch(JSON.parse(data) as AppEvent);
  } catch {
    /* heartbeat comments and partial frames are expected */
  }
}

function scheduleReconnect(): void {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, reconnectDelay);
  // Backoff capped at 15s: a local backend restart takes a few seconds, so
  // waiting longer just makes the UI feel dead.
  reconnectDelay = Math.min(15000, reconnectDelay * 1.8);
}

export function disconnect(): void {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  source?.close();
  source = null;
  setState("closed");
}

/** Subscribe to one event type, or `"*"` for all. Returns an unsubscribe fn. */
export function on<P = Record<string, unknown>>(
  type: EventType | "*",
  handler: (event: AppEvent<P>) => void,
): () => void {
  const set = handlers.get(type) ?? new Set<Handler>();
  set.add(handler as Handler);
  handlers.set(type, set);
  connect();
  return () => {
    set.delete(handler as Handler);
  };
}

export function onConnectionChange(handler: ConnectionHandler): () => void {
  connectionHandlers.add(handler);
  handler(state);
  return () => connectionHandlers.delete(handler);
}

export function connectionState(): ConnectionState {
  return state;
}

/** Resolve when a job finishes. Rejects with the backend's error message. */
export function waitForJob(
  jobId: string,
  onProgress?: (progress: number, message: string) => void,
): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const unsubscribers: (() => void)[] = [];
    let settled = false;
    let polling = false;
    let pollTimer: ReturnType<typeof setInterval> | null = null;
    const cleanup = () => {
      unsubscribers.forEach((u) => u());
      if (pollTimer) clearInterval(pollTimer);
    };
    const succeed = (result: Record<string, unknown>) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(result);
    };
    const fail = (message: string, failure: Record<string, unknown>) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(new JobFailureError(message, failure));
    };
    const consumeSnapshot = (job: JobRecord) => {
      onProgress?.(job.progress || 0, job.message || "");
      if (job.status === "done") succeed(job.result || {});
      if (job.status === "failed") {
        const failure = (job.result?.failure as Record<string, unknown> | undefined) ?? {};
        fail(job.error || "the job failed", failure);
      }
      if (job.status === "cancelled") {
        fail(job.error || "the job was cancelled", {
          outcome: "cancelled",
          error_code: "cancelled",
          retryable: false,
          cancelled: true,
        });
      }
    };

    unsubscribers.push(
      on<{ progress: number; message: string }>("job.progress", (event) => {
        if (event.jobId === jobId) onProgress?.(event.payload.progress, event.payload.message);
      }),
      on<{ result: Record<string, unknown> }>("job.done", (event) => {
        if (event.jobId !== jobId) return;
        succeed(event.payload.result || {});
      }),
      on<Record<string, unknown> & { error: string; cancelled?: boolean }>("job.failed", (event) => {
        if (event.jobId !== jobId) return;
        fail(event.payload.error || "the job failed", event.payload);
      }),
    );

    // Subscribe first, then reconcile against durable state.  A small import
    // can finish between POST /import and EventSource subscription; polling
    // also survives replay-buffer loss and a renderer reconnect.
    const poll = async () => {
      if (settled || polling) return;
      polling = true;
      try {
        consumeSnapshot(await api.get<JobRecord>(`/api/system/jobs/${jobId}`));
      } catch {
        // SSE may still deliver the terminal event; transient backend restarts
        // are normal and should not turn a running job into a false failure.
      } finally {
        polling = false;
      }
    };
    void poll();
    pollTimer = setInterval(() => void poll(), 1000);
  });
}

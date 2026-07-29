/**
 * HTTP client for the PaperCreator backend.
 *
 * Two things it does beyond `fetch`:
 *
 * 1. **Unwraps the backend's error envelope.** Every deliberate failure arrives as
 *    `{error: {code, message, details}}`; this turns that into an `ApiError`
 *    carrying the code, so UI code can branch on `err.code === "dependency_missing"`
 *    instead of matching on message text.
 * 2. **Resolves the base URL once.** In the Vite dev server `/api` is proxied, so a
 *    relative URL is correct. In the packaged app the page is `file://`, where a
 *    relative URL has no host - so the origin the main process reports is used.
 */

const DEFAULT_ORIGIN = "http://127.0.0.1:8765";

let baseUrl = "";

/** Backend origin, or `""` when relative URLs work (dev server / same origin). */
export function resolveBaseUrl(): string {
  if (baseUrl) return baseUrl;
  if (typeof window !== "undefined" && window.location.protocol === "file:") {
    baseUrl = DEFAULT_ORIGIN;
  }
  return baseUrl;
}

export function setBaseUrl(origin: string): void {
  baseUrl = origin.replace(/\/$/, "");
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;

  constructor(message: string, code: string, status: number, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }

  /** True when the fix is installing an optional Python package. */
  get isMissingDependency(): boolean {
    return this.code === "dependency_missing";
  }

  /** True when the fix is a settings change (missing key, no model configured). */
  get isConfiguration(): boolean {
    return (
      this.code === "configuration_error" ||
      this.code === "provider_unavailable" ||
      this.code === "llm_configuration_error" ||
      this.code === "llm_authentication_error" ||
      this.code === "llm_unavailable"
    );
  }

  /** The hint the backend attached, if any - already written for a user. */
  get hint(): string {
    return String(this.details.hint || this.details.action || "");
  }
}

type Query = Record<string, string | number | boolean | undefined | null>;

function buildUrl(path: string, query?: Query): string {
  const origin = resolveBaseUrl();
  const url = `${origin}${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.append(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

async function parse<T>(response: Response): Promise<T> {
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      // A non-JSON body from a local service means something crashed outside the
      // app's own error handling; surface the raw text, truncated.
      if (!response.ok) {
        throw new ApiError(text.slice(0, 400) || response.statusText, "bad_response", response.status);
      }
      return text as unknown as T;
    }
  }
  if (!response.ok) {
    const envelope = (payload as { error?: { code?: string; message?: string; details?: Record<string, unknown> } })?.error;
    throw new ApiError(
      envelope?.message || response.statusText || `HTTP ${response.status}`,
      envelope?.code || "http_error",
      response.status,
      envelope?.details || {},
    );
  }
  return payload as T;
}

async function request<T>(
  method: string,
  path: string,
  options: { query?: Query; body?: unknown; signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<T> {
  const { query, body, signal, timeoutMs } = options;
  // Long operations (sync search, sync analysis, LaTeX build) legitimately take
  // minutes, so there is no global timeout - callers opt in per request.
  const controller = timeoutMs ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
  try {
    const response = await fetch(buildUrl(path, query), {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: signal ?? controller?.signal,
    });
    return await parse<T>(response);
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if ((error as Error).name === "AbortError") {
      throw new ApiError("the request was cancelled or timed out", "aborted", 0);
    }
    // A network-level failure against a local service almost always means the
    // backend is not running, which is worth saying explicitly.
    throw new ApiError(
      `Cannot reach the backend at ${resolveBaseUrl() || window.location.origin}. ` +
        `It may still be starting, or it may have crashed - check the Output panel.`,
      "backend_unreachable",
      0,
      { original: String(error) },
    );
  } finally {
    if (timer) clearTimeout(timer);
  }
}

export const api = {
  get: <T>(path: string, query?: Query, signal?: AbortSignal) =>
    request<T>("GET", path, { query, signal }),
  post: <T>(path: string, body?: unknown, query?: Query, signal?: AbortSignal) =>
    request<T>("POST", path, { body, query, signal }),
  patch: <T>(path: string, body?: unknown, query?: Query) =>
    request<T>("PATCH", path, { body, query }),
  put: <T>(path: string, body?: unknown, query?: Query) =>
    request<T>("PUT", path, { body, query }),
  delete: <T>(path: string, query?: Query, body?: unknown) =>
    request<T>("DELETE", path, { query, body }),
  /** Absolute URL builder, for links and downloads handed to the shell. */
  url: buildUrl,
};

/** Wait for the backend to answer its health endpoint. */
export async function waitForBackend(timeoutMs = 60000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await api.get("/api/system/health");
      return true;
    } catch {
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  return false;
}

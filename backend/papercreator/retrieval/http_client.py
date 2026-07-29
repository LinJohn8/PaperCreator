"""Shared HTTP client: rate limiting, retries, response caching.

Every provider goes through one :class:`HttpClient`. Centralising it means:

* **Terms of use are respected globally.** arXiv asks for one request every
  three seconds; if each provider instance kept its own limiter, two concurrent
  searches would double that. The limiter is keyed by provider id and shared
  across the process.
* **Retries are uniform.** 429/5xx get exponential backoff with jitter and
  honour ``Retry-After``. 4xx (other than 429) are not retried - they are
  request bugs, not transient failures.
* **Caching is free for providers.** Scholarly metadata barely changes, so
  identical requests within the TTL are served from disk. This makes
  re-running a search during development instant and keeps load off free APIs.

Cache entries are JSON files under ``<home>/cache/http/<provider>/<hash>.json``,
keyed by method+url+body. Deleting the directory is always safe.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any

import httpx

from ..core.config import get_settings
from ..core.errors import ProviderError, RateLimitError
from ..core.logging_setup import get_logger
from ..core.paths import get_paths
from ..core.util import stable_hash, utc_now_iso

log = get_logger(__name__)


class RateLimiter:
    """Async token-gate enforcing a minimum interval and a concurrency cap.

    One instance per provider id, created on demand and kept for the process
    lifetime by :class:`HttpClient`.
    """

    def __init__(self, min_interval_s: float, max_concurrency: int = 2) -> None:
        self.min_interval_s = max(0.0, min_interval_s)
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def __aenter__(self) -> "RateLimiter":
        await self._semaphore.acquire()
        async with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self.min_interval_s
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self._semaphore.release()


class ResponseCache:
    """Filesystem cache for provider responses."""

    def __init__(self, root: Path, ttl_hours: int) -> None:
        self.root = root
        self.ttl_s = max(0, ttl_hours) * 3600

    def _path(self, provider: str, key: str) -> Path:
        # Two-level fan-out keeps directories small on large libraries.
        return self.root / provider / key[:2] / f"{key}.json"

    def get(self, provider: str, key: str) -> Any | None:
        if self.ttl_s <= 0:
            return None
        path = self._path(provider, key)
        if not path.is_file():
            return None
        try:
            if time.time() - path.stat().st_mtime > self.ttl_s:
                return None
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Corrupt entry: treat as a miss and let it be overwritten.
            return None
        return payload.get("body")

    def put(self, provider: str, key: str, body: Any, meta: dict[str, Any]) -> None:
        if self.ttl_s <= 0:
            return
        path = self._path(provider, key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {"cached_at": utc_now_iso(), "meta": meta, "body": body},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            log.debug("cache write failed for %s/%s: %s", provider, key, exc)

    def clear(self, provider: str = "") -> int:
        target = self.root / provider if provider else self.root
        if not target.exists():
            return 0
        removed = 0
        for path in target.rglob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def stats(self) -> dict[str, Any]:
        if not self.root.exists():
            return {"entries": 0, "size_bytes": 0, "by_provider": {}}
        by_provider: dict[str, int] = {}
        total_size = entries = 0
        for path in self.root.rglob("*.json"):
            try:
                total_size += path.stat().st_size
            except OSError:
                continue
            entries += 1
            provider = path.relative_to(self.root).parts[0]
            by_provider[provider] = by_provider.get(provider, 0) + 1
        return {"entries": entries, "size_bytes": total_size,
                "by_provider": by_provider}


class HttpClient:
    """Rate-limited, cached, retrying HTTP facade for providers."""

    def __init__(
        self,
        *,
        timeout_s: float | None = None,
        use_cache: bool | None = None,
        cache_ttl_hours: int | None = None,
    ) -> None:
        settings = get_settings()
        self.timeout_s = timeout_s if timeout_s is not None else settings.retrieval.http_timeout_s
        self.use_cache = (
            use_cache if use_cache is not None else settings.retrieval.use_cache
        )
        ttl = (
            cache_ttl_hours if cache_ttl_hours is not None
            else settings.retrieval.cache_ttl_hours
        )
        self.cache = ResponseCache(get_paths().http_cache_dir, ttl)
        self.user_agent = settings.user_agent()
        self._limiters: dict[str, RateLimiter] = {}
        self._client: httpx.AsyncClient | None = None
        self.cache_hits = 0
        self.cache_misses = 0

    # ------------------------------------------------------------ lifecycle
    async def __aenter__(self) -> "HttpClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_s, connect=15.0),
            follow_redirects=True,
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            # Providers are hit in bursts; a small pool avoids opening a socket
            # per request while staying well under any source's limits.
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def limiter_for(self, provider: str, min_interval_s: float, concurrency: int) -> RateLimiter:
        limiter = self._limiters.get(provider)
        if limiter is None:
            limiter = RateLimiter(min_interval_s, concurrency)
            self._limiters[provider] = limiter
        return limiter

    # -------------------------------------------------------------- request
    async def request(
        self,
        provider: str,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
        min_interval_s: float = 0.34,
        concurrency: int = 2,
        max_retries: int = 3,
        expect: str = "json",
        cacheable: bool = True,
        cache_key_extra: str = "",
    ) -> Any:
        """Perform one request. Returns parsed JSON, or text when ``expect='text'``.

        Raises :class:`RateLimitError` when a provider keeps refusing after
        retries, and :class:`ProviderError` for other HTTP failures - both of
        which ``Provider.safe_search`` turns into per-provider stats.
        """
        if self._client is None:
            raise RuntimeError("HttpClient must be used as an async context manager")

        cache_key = stable_hash(
            method.upper(), url, json.dumps(params or {}, sort_keys=True),
            json.dumps(json_body, sort_keys=True) if json_body is not None else "",
            cache_key_extra, length=32,
        )
        if self.use_cache and cacheable and method.upper() == "GET":
            cached = self.cache.get(provider, cache_key)
            if cached is not None:
                self.cache_hits += 1
                log.debug("cache hit %s %s", provider, url)
                return cached
            self.cache_misses += 1

        limiter = self.limiter_for(provider, min_interval_s, concurrency)
        last_error: str = ""
        for attempt in range(max_retries + 1):
            async with limiter:
                try:
                    response = await self._client.request(
                        method.upper(), url, params=params, json=json_body,
                        data=data, headers=headers,
                    )
                except httpx.TimeoutException:
                    last_error = "timeout"
                    if attempt >= max_retries:
                        raise
                    await self._backoff(attempt)
                    continue
                except httpx.HTTPError as exc:
                    last_error = str(exc)
                    if attempt >= max_retries:
                        raise
                    await self._backoff(attempt)
                    continue

            if response.status_code == 429:
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                last_error = f"HTTP 429 (retry-after={retry_after or 'n/a'})"
                if attempt >= max_retries:
                    raise RateLimitError(
                        f"{provider} rate limit exceeded after {attempt + 1} attempts",
                        details={
                            "provider": provider,
                            "url": url,
                            "category": "rate_limited",
                            "http_status": 429,
                            "retryable": True,
                            "retry_after_s": retry_after or None,
                        },
                    )
                await asyncio.sleep(retry_after if retry_after else 0.0)
                await self._backoff(attempt)
                continue

            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                if attempt >= max_retries:
                    raise ProviderError(
                        f"{provider} returned {response.status_code}",
                        details={
                            "provider": provider,
                            "url": url,
                            "body": response.text[:500],
                            "category": "http_error",
                            "http_status": response.status_code,
                            "retryable": True,
                        },
                    )
                await self._backoff(attempt)
                continue

            if response.status_code >= 400:
                # Client errors are deterministic; retrying wastes the budget.
                raise ProviderError(
                    f"{provider} returned {response.status_code}",
                    details={
                        "provider": provider,
                        "url": url,
                        "body": response.text[:500],
                        "category": (
                            "authentication_error"
                            if response.status_code in (401, 403)
                            else "http_error"
                        ),
                        "http_status": response.status_code,
                        "retryable": False,
                    },
                )

            body: Any
            if expect == "json":
                try:
                    body = response.json()
                except ValueError as exc:
                    raise ProviderError(
                        f"{provider} returned non-JSON body",
                        details={
                            "provider": provider,
                            "url": url,
                            "error": str(exc),
                            "snippet": response.text[:300],
                            "category": "invalid_response",
                            "http_status": response.status_code,
                            "retryable": True,
                        },
                    ) from exc
            else:
                body = response.text

            if self.use_cache and cacheable and method.upper() == "GET":
                self.cache.put(
                    provider, cache_key, body,
                    {"url": url, "status": response.status_code},
                )
            return body

        raise ProviderError(
            f"{provider} request failed: {last_error or 'unknown error'}",
            details={"provider": provider, "url": url},
        )

    async def get_json(self, provider: str, url: str, **kwargs: Any) -> Any:
        return await self.request(provider, "GET", url, expect="json", **kwargs)

    async def get_text(self, provider: str, url: str, **kwargs: Any) -> str:
        return await self.request(provider, "GET", url, expect="text", **kwargs)

    async def post_json(self, provider: str, url: str, **kwargs: Any) -> Any:
        return await self.request(
            provider, "POST", url, expect="json", cacheable=False, **kwargs
        )

    async def download(self, url: str, target: Path, *, provider: str = "download") -> Path:
        """Stream a file (PDF) to disk. Skips the cache entirely."""
        if self._client is None:
            raise RuntimeError("HttpClient must be used as an async context manager")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".part")
        async with self.limiter_for(provider, 0.5, 2):
            async with self._client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise ProviderError(
                        f"download failed with HTTP {response.status_code}",
                        details={"url": url},
                    )
                with temp.open("wb") as handle:
                    async for chunk in response.aiter_bytes(65536):
                        handle.write(chunk)
        temp.replace(target)
        return target

    @staticmethod
    async def _backoff(attempt: int) -> None:
        """Exponential backoff with jitter: ~0.5s, 1s, 2s, capped at 8s.

        Jitter matters when several providers are retrying at once - without it
        they synchronise and hammer in lockstep.
        """
        delay = min(8.0, 0.5 * (2 ** attempt))
        await asyncio.sleep(delay * (0.5 + random.random() * 0.5))

    def stats(self) -> dict[str, Any]:
        return {
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_enabled": self.use_cache,
        }


def _parse_retry_after(value: str | None) -> float:
    """``Retry-After`` in seconds. Ignores the HTTP-date form (rare, and a
    wrong parse there would sleep for hours)."""
    if not value:
        return 0.0
    try:
        seconds = float(value.strip())
    except ValueError:
        return 0.0
    return min(60.0, max(0.0, seconds))

"""Retrieval provider contract.

Adding a new source means writing one subclass of :class:`Provider` and
registering it - nothing else in the system needs to change. That is the point
of this module: the user asked for "many different search methods, added over
time", so the seam has to be narrow and honest about what each source can do.

A provider must:

1. declare :class:`ProviderCapabilities` truthfully (the pipeline post-filters
   whatever a provider cannot filter server-side);
2. declare its :class:`RateLimit` (the shared limiter enforces it globally, so
   two concurrent searches cannot together exceed a source's terms of use);
3. implement :meth:`search` and convert results to :class:`Paper`;
4. never raise for "no results" - only for genuine failures.

Failures are contained: :meth:`safe_search` converts any exception into a
:class:`ProviderStats` with an ``error``, so one dead source never fails a
multi-source search.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..core.errors import ProviderError, ProviderUnavailableError, RateLimitError
from ..core.logging_setup import get_logger
from ..core.models import Paper, ProviderStats, SearchRequest

log = get_logger(__name__)


@dataclass(frozen=True)
class RateLimit:
    """Per-provider request budget.

    ``min_interval_s`` is the hard floor between two requests to the same host.
    ``max_retries`` matters as much as the interval: a source that answers 429
    is already unhappy, and retrying it three times makes things worse. DBLP was
    observed to stop accepting connections entirely after a burst, so
    throttle-sensitive providers set this to 0 or 1 and fail fast - the pipeline
    reports the source as unavailable for that search and moves on.

    ``max_queries`` caps how many expanded query variants a single search sends
    to this provider, bounding total requests regardless of expansion size.
    """

    min_interval_s: float = 0.34
    max_concurrency: int = 2
    max_retries: int = 3
    max_queries: int = 3
    # Informational: shown in the UI so the user understands slow providers.
    note: str = ""

    def request_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for :meth:`HttpClient.request`."""
        return {
            "min_interval_s": self.min_interval_s,
            "concurrency": self.max_concurrency,
            "max_retries": self.max_retries,
        }


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a provider can do server-side.

    The pipeline reads this to decide what it must do itself. For example
    arXiv cannot filter by citation count, so ``sort_by_citations=False`` means
    the pipeline sorts locally after merging.
    """

    full_text_search: bool = True
    field_search: bool = False       # title:/author:/abstract: style queries
    boolean_operators: bool = False
    year_range: bool = False
    open_access_filter: bool = False
    venue_filter: bool = False
    author_filter: bool = False
    fields_of_study_filter: bool = False
    sort_by_date: bool = False
    sort_by_citations: bool = False
    returns_abstract: bool = True
    returns_citations: bool = False
    returns_references: bool = False
    returns_pdf_url: bool = False
    max_results_per_request: int = 100
    supports_pagination: bool = True
    # True when the source itself does semantic/embedding matching, so passing
    # a whole abstract as the query is meaningful.
    semantic_query: bool = False


@dataclass
class ProviderAvailability:
    """Whether this provider can be used right now, and why not."""

    available: bool
    reason: str = ""
    needs_key: bool = False
    key_setting: str = ""
    signup_url: str = ""


@dataclass
class ProviderMeta:
    """Static description, surfaced in the UI provider picker."""

    id: str
    name: str
    name_zh: str = ""
    description: str = ""
    description_zh: str = ""
    homepage: str = ""
    docs_url: str = ""
    # free      - no key, no cost
    # freemium  - works keyless but a free key raises limits
    # key       - unusable without a key
    tier: str = "free"
    coverage: str = ""
    disciplines: list[str] = field(default_factory=list)
    requires_key: bool = False
    key_setting: str = ""
    signup_url: str = ""


class Provider(ABC):
    """Base class for every retrieval source."""

    meta: ProviderMeta
    capabilities = ProviderCapabilities()
    rate_limit = RateLimit()

    def __init__(self, client: "HttpClient") -> None:
        self.client = client

    # ------------------------------------------------------------ interface
    @property
    def id(self) -> str:
        return self.meta.id

    def availability(self) -> ProviderAvailability:
        """Default: available unless a required key is missing."""
        if self.meta.requires_key and not self.api_key():
            return ProviderAvailability(
                available=False,
                reason=f"{self.meta.name} requires an API key",
                needs_key=True,
                key_setting=self.meta.key_setting,
                signup_url=self.meta.signup_url,
            )
        return ProviderAvailability(available=True)

    def api_key(self) -> str:
        from ..core.config import get_settings

        if not self.meta.key_setting:
            return ""
        return get_settings().provider_keys.get(self.meta.key_setting)

    @abstractmethod
    async def search(self, request: SearchRequest, limit: int) -> list[Paper]:
        """Run one search and return parsed papers.

        Implementations receive the *resolved* limit for this provider (already
        clamped to ``capabilities.max_results_per_request`` by the pipeline when
        pagination is unsupported). They should honour every filter they declare
        support for and ignore the rest.
        """

    async def fetch_by_id(self, external_id: str) -> Paper | None:
        """Optional single-record lookup, used by "resolve this DOI/arXiv id".

        Providers that cannot do this return ``None`` rather than raising.
        """
        return None

    # -------------------------------------------------------------- helpers
    async def safe_search(
        self, request: SearchRequest, limit: int
    ) -> tuple[list[Paper], ProviderStats]:
        """Run :meth:`search`, converting any failure into stats.

        This is what the pipeline calls. The contract it guarantees to callers:
        never raises, always returns stats, always tags results with this
        provider id.
        """
        started = time.perf_counter()
        availability = self.availability()
        if not availability.available:
            return [], ProviderStats(
                provider=self.id,
                outcome="unavailable",
                error=availability.reason or "unavailable",
                error_code="provider_unavailable",
                hint="Configure this source or deselect it and retry with another provider.",
            )
        try:
            papers = await self.search(request, limit)
        except RateLimitError as exc:
            log.warning("provider %s rate limited: %s", self.id, exc)
            return [], ProviderStats(
                provider=self.id,
                duration_ms=int((time.perf_counter() - started) * 1000),
                outcome="rate_limited",
                error=f"rate limited: {exc.message}",
                error_code=exc.code,
                retryable=True,
                http_status=429,
                retry_after_s=exc.details.get("retry_after_s"),
                hint="Wait for the provider quota to recover, then retry this source.",
            )
        except ProviderUnavailableError as exc:
            log.warning("provider %s failed: %s", self.id, exc)
            return [], ProviderStats(
                provider=self.id,
                duration_ms=int((time.perf_counter() - started) * 1000),
                outcome="unavailable",
                error=exc.message,
                error_code=exc.code,
                retryable=False,
                hint="Configure this source or deselect it and retry with another provider.",
            )
        except ProviderError as exc:
            log.warning("provider %s failed: %s", self.id, exc)
            category = str(exc.details.get("category") or "provider_error")
            allowed = {
                "authentication_error", "http_error", "invalid_response",
                "provider_error",
            }
            outcome = category if category in allowed else "provider_error"
            status = exc.details.get("http_status")
            retryable = bool(exc.details.get("retryable", False))
            hint = {
                "authentication_error": (
                    "Check this provider's API key or account permission in Settings."
                ),
                "http_error": (
                    "Retry later if the provider is unavailable; repeated 4xx errors "
                    "usually require changing the query or provider configuration."
                ),
                "invalid_response": (
                    "Retry once; if this repeats, the provider response format may have changed."
                ),
            }.get(outcome, "Retry this source or deselect it and use another provider.")
            return [], ProviderStats(
                provider=self.id,
                duration_ms=int((time.perf_counter() - started) * 1000),
                outcome=outcome,
                error=exc.message,
                error_code=exc.code,
                retryable=retryable,
                http_status=int(status) if isinstance(status, int) else None,
                hint=hint,
            )
        except httpx.TimeoutException:
            return [], ProviderStats(
                provider=self.id,
                duration_ms=int((time.perf_counter() - started) * 1000),
                outcome="timeout",
                error="timed out",
                error_code="timeout",
                retryable=True,
                hint="Check the network and retry this source; other providers can still succeed.",
            )
        except httpx.HTTPError as exc:
            return [], ProviderStats(
                provider=self.id,
                duration_ms=int((time.perf_counter() - started) * 1000),
                outcome="network_error",
                error=f"network error: {exc}",
                error_code="network_error",
                retryable=True,
                hint="Check DNS, proxy and firewall settings, then retry this source.",
            )
        except Exception as exc:  # noqa: BLE001 - a bad parse must not kill the search
            log.exception("provider %s raised unexpectedly", self.id)
            return [], ProviderStats(
                provider=self.id,
                duration_ms=int((time.perf_counter() - started) * 1000),
                outcome="unexpected_error",
                error=f"{type(exc).__name__}: {exc}",
                error_code="unexpected_error",
                retryable=False,
                hint="The provider parser failed unexpectedly; check logs before retrying.",
            )

        for paper in papers:
            if self.id not in paper.source_providers:
                paper.source_providers.append(self.id)
            paper.ensure_id()
        truncated = len(papers) > limit
        return papers[:limit], ProviderStats(
            provider=self.id,
            count=min(len(papers), limit),
            duration_ms=int((time.perf_counter() - started) * 1000),
            truncated=truncated,
        )

    def describe(self) -> dict[str, Any]:
        """Serialisable description for ``GET /api/search/providers``."""
        availability = self.availability()
        return {
            "id": self.meta.id,
            "name": self.meta.name,
            "name_zh": self.meta.name_zh,
            "description": self.meta.description,
            "description_zh": self.meta.description_zh,
            "homepage": self.meta.homepage,
            "docs_url": self.meta.docs_url,
            "tier": self.meta.tier,
            "coverage": self.meta.coverage,
            "disciplines": self.meta.disciplines,
            "requires_key": self.meta.requires_key,
            "key_setting": self.meta.key_setting,
            "signup_url": self.meta.signup_url,
            "available": availability.available,
            "unavailable_reason": availability.reason,
            "has_key": bool(self.api_key()) if self.meta.key_setting else False,
            "capabilities": {
                "full_text_search": self.capabilities.full_text_search,
                "field_search": self.capabilities.field_search,
                "boolean_operators": self.capabilities.boolean_operators,
                "year_range": self.capabilities.year_range,
                "open_access_filter": self.capabilities.open_access_filter,
                "venue_filter": self.capabilities.venue_filter,
                "author_filter": self.capabilities.author_filter,
                "sort_by_date": self.capabilities.sort_by_date,
                "sort_by_citations": self.capabilities.sort_by_citations,
                "returns_abstract": self.capabilities.returns_abstract,
                "returns_citations": self.capabilities.returns_citations,
                "returns_references": self.capabilities.returns_references,
                "returns_pdf_url": self.capabilities.returns_pdf_url,
                "max_results_per_request": self.capabilities.max_results_per_request,
                "supports_pagination": self.capabilities.supports_pagination,
                "semantic_query": self.capabilities.semantic_query,
            },
            "rate_limit": {
                "min_interval_s": self.rate_limit.min_interval_s,
                "max_concurrency": self.rate_limit.max_concurrency,
                "max_retries": self.rate_limit.max_retries,
                "max_queries": self.rate_limit.max_queries,
                "note": self.rate_limit.note,
            },
        }


# Imported at the end to avoid a circular import at module load: http_client
# imports nothing from base, but base's type hint needs the name.
from .http_client import HttpClient  # noqa: E402,F401

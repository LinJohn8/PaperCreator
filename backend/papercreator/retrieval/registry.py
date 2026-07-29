"""Provider registry.

Providers are instantiated per :class:`HttpClient` (i.e. per search execution)
because they hold no state beyond the client. This module also answers "which
providers should run for this request?", applying, in order:

1. an explicit list on the request,
2. the user's enabled set from settings,
3. availability (missing key -> retained as a structured unavailable outcome).
"""

from __future__ import annotations

from typing import Any

from ..core.config import get_settings
from ..core.errors import NotFoundError
from ..core.logging_setup import get_logger
from .http_client import HttpClient
from .providers import PROVIDER_CLASSES

log = get_logger(__name__)

# id -> class, built once from the declared metadata.
_BY_ID = {cls.meta.id: cls for cls in PROVIDER_CLASSES}


def provider_ids() -> list[str]:
    return list(_BY_ID.keys())


def build(provider_id: str, client: HttpClient) -> Any:
    cls = _BY_ID.get(provider_id)
    if cls is None:
        raise NotFoundError(
            f"unknown retrieval provider '{provider_id}'",
            details={"known": provider_ids()},
        )
    return cls(client)


def build_all(client: HttpClient) -> dict[str, Any]:
    return {pid: cls(client) for pid, cls in _BY_ID.items()}


def resolve_selection(
    requested: list[str], client: HttpClient
) -> tuple[list[Any], list[str]]:
    """Decide which providers run.

    Returns ``(providers, warnings)``. Unknown ids are excluded. A known but
    unavailable provider remains selected: :meth:`Provider.safe_search` turns
    it into structured stats without performing I/O, so history, SSE and the UI
    use the same failure contract as network errors.
    """
    settings = get_settings()
    warnings: list[str] = []

    if requested:
        wanted = []
        for pid in requested:
            if pid in _BY_ID:
                wanted.append(pid)
            else:
                warnings.append(f"unknown provider '{pid}' ignored")
    else:
        wanted = [p for p in settings.retrieval.enabled_providers if p in _BY_ID]
        if not wanted:
            # Never leave the user with zero sources because of a bad config.
            wanted = ["arxiv", "openalex", "crossref"]
            warnings.append(
                "no enabled providers in settings; using arxiv, openalex, crossref"
            )

    selected: list[Any] = []
    for pid in dict.fromkeys(wanted):
        provider = build(pid, client)
        availability = provider.availability()
        if not availability.available:
            warnings.append(f"{provider.meta.name} unavailable: {availability.reason}")
            selected.append(provider)
            continue
        if availability.reason:
            # Available but degraded (e.g. S2 without a key).
            warnings.append(f"{provider.meta.name}: {availability.reason}")
        selected.append(provider)
    return selected, warnings


def describe_all() -> list[dict[str, Any]]:
    """Full provider catalogue for ``GET /api/search/providers``.

    Builds a throwaway client: ``describe`` only reads settings and metadata,
    never performs I/O.
    """
    client = HttpClient()
    enabled = set(get_settings().retrieval.enabled_providers)
    out = []
    for pid, cls in _BY_ID.items():
        description = cls(client).describe()
        description["enabled"] = pid in enabled
        out.append(description)
    # Available and enabled first, so the UI list reads usefully by default.
    out.sort(key=lambda d: (not d["available"], not d["enabled"], d["name"]))
    return out

"""Settings routes: read, patch, test providers.

Secrets never leave the backend in readable form. ``GET`` returns them masked as
``***set***``; ``PATCH`` treats that same marker as "unchanged", so the UI can
round-trip the whole settings object without the user retyping every key.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ...core.config import (
    configuration_sources,
    get_settings,
    load_dotenv_file,
    reload_settings,
)
from ...core.errors import ValidationError
from ...core.logging_setup import get_logger
from ...store import settings_store

log = get_logger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def read_settings() -> dict[str, Any]:
    """Effective settings with secrets masked."""
    return settings_store.effective_settings()


@router.get("/sources")
def read_setting_sources() -> dict[str, Any]:
    """Configuration precedence and active field names, never field values."""
    return configuration_sources()


class SettingsPatch(BaseModel):
    """A partial settings update.

    Deliberately untyped per-section (``dict[str, Any]``) so the UI can send any
    subset without this schema having to mirror every field; validation happens
    in :class:`~papercreator.core.config.Settings` when the merged result is
    parsed, which is the single source of truth for the shape.
    """

    server: dict[str, Any] | None = None
    identity: dict[str, Any] | None = None
    provider_keys: dict[str, Any] | None = None
    llm: dict[str, Any] | None = None
    retrieval: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None
    writing: dict[str, Any] | None = None
    assistant: dict[str, Any] | None = None
    ui: dict[str, Any] | None = None
    overleaf: dict[str, Any] | None = None


@router.patch("")
def update_settings(patch: SettingsPatch) -> dict[str, Any]:
    """Merge a partial update into settings.json / secrets.json."""
    payload = {k: v for k, v in patch.model_dump().items() if v is not None}
    if not payload:
        raise ValidationError("no settings were provided")
    try:
        return settings_store.update_settings(payload)
    except Exception as exc:  # noqa: BLE001 - surface validation as a 4xx
        raise ValidationError(f"settings update rejected: {exc}") from exc


@router.post("/reload")
def reload() -> dict[str, Any]:
    """Re-read settings from disk and environment.

    Useful after hand-editing ``settings.json`` or ``.env`` while running.
    """
    load_dotenv_file()
    reload_settings()
    return settings_store.effective_settings()


@router.delete("/secret")
def delete_secret(path: str = Query(..., description="dotted path, e.g. provider_keys.openalex")) -> dict[str, Any]:
    """Clear one stored secret."""
    if path not in settings_store.SECRET_PATHS and not path.startswith("llm.providers"):
        raise ValidationError(
            f"'{path}' is not a secret setting. Known: "
            f"{', '.join(settings_store.SECRET_PATHS)}"
        )
    return settings_store.delete_secret(path)


# ------------------------------------------------------------------ providers


@router.get("/llm/providers")
async def list_llm_providers(
    probe: bool = Query(False, description="query each endpoint for its models")
) -> dict[str, Any]:
    """Configured LLM providers. ``probe=true`` is a live connectivity check."""
    from ...llm import registry as llm_registry

    return {
        "providers": await llm_registry.describe_all(probe=probe),
        "roles": llm_registry.role_defaults(),
        "supported_kinds": sorted(
            __import__(
                "papercreator.llm.backends", fromlist=["BACKENDS"]
            ).BACKENDS
        ),
    }


class ProviderTest(BaseModel):
    provider: str
    model: str = ""


@router.post("/llm/test")
async def test_llm_provider(request: ProviderTest) -> dict[str, Any]:
    """Send a one-token round trip to verify a provider actually works.

    Returns ``ok: false`` with the error rather than raising: a failed test is
    the expected answer to "is this key right?", not an exception.
    """
    from ...llm import client as llm_client

    return await llm_client.test_provider(request.provider, request.model)


class ProviderUpsert(BaseModel):
    id: str
    kind: str = "openai"
    label: str = ""
    base_url: str = ""
    api_key: str = ""
    default_model: str = ""
    models: list[str] = Field(default_factory=list)
    enabled: bool = True
    timeout_s: float = 300.0
    price_in_per_mtok: float = 0.0
    price_out_per_mtok: float = 0.0


@router.put("/llm/providers/{provider_id}")
def upsert_llm_provider(provider_id: str, request: ProviderUpsert) -> dict[str, Any]:
    """Add or update one LLM provider.

    Routed through the settings merge so the key lands in secrets.json and the
    rest in settings.json.
    """
    payload = request.model_dump()
    payload["id"] = provider_id
    return settings_store.update_settings({"llm": {"providers": [payload]}})


@router.get("/retrieval/providers")
def list_retrieval_providers() -> dict[str, Any]:
    from ...retrieval import registry as retrieval_registry

    return {
        "providers": retrieval_registry.describe_all(),
        "enabled": get_settings().retrieval.enabled_providers,
    }


class EnabledProviders(BaseModel):
    provider_ids: list[str]


@router.put("/retrieval/enabled")
def set_enabled_providers(request: EnabledProviders) -> dict[str, Any]:
    """Choose which retrieval sources searches use by default."""
    from ...retrieval import registry as retrieval_registry

    known = set(retrieval_registry.provider_ids())
    unknown = [p for p in request.provider_ids if p not in known]
    if unknown:
        raise ValidationError(
            f"unknown provider(s): {', '.join(unknown)}. Known: "
            f"{', '.join(sorted(known))}"
        )
    if not request.provider_ids:
        raise ValidationError("at least one retrieval provider must stay enabled")
    return settings_store.update_settings(
        {"retrieval": {"enabled_providers": request.provider_ids}}
    )


@router.get("/analysis/backends")
def analysis_backends() -> dict[str, Any]:
    """Embedding/reducer/clusterer availability, including model-host state."""
    from ...analysis import cluster, embeddings, gaps, reduce

    return {
        "embedding_backends": embeddings.describe_backends(),
        "reducers": reduce.describe_reducers(),
        "clusterers": cluster.describe_clusterers(),
        "gap_detectors": gaps.describe_detectors(),
        "current": get_settings().analysis.model_dump(),
    }


@router.post("/analysis/probe-model-host")
def probe_model_host() -> dict[str, Any]:
    """Re-check whether the embedding model host is reachable.

    Exposed because the result is cached for five minutes; after the user sets a
    mirror they need an immediate answer rather than waiting for expiry.
    """
    from ...analysis import embeddings

    reachable = embeddings.endpoint_reachable(force=True)
    return {
        "endpoint": embeddings.hf_endpoint(),
        "reachable": reachable,
        "model_cached": embeddings.model_is_cached(
            get_settings().analysis.sentence_transformer_model
        ),
        "blocker": embeddings.sentence_transformers_blocker(),
        "hint": (
            "" if reachable else
            "Try https://hf-mirror.com as the mirror if huggingface.co is blocked "
            "on your network."
        ),
    }


@router.get("/overleaf")
def overleaf_status() -> dict[str, Any]:
    from ...convert import overleaf

    return overleaf.status()

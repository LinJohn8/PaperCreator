"""LLM provider registry and model resolution.

Models are addressed as ``"provider_id:model_name"`` throughout the app
(``"openai:gpt-4o-mini"``, ``"ollama:qwen2.5"``). :func:`resolve` accepts that,
a bare provider id (uses its default model), a bare model name (searches
configured providers), or nothing at all (uses the configured default).

Role defaults let the rest of the codebase ask for a *capability* rather than a
model: agents request ``chat``, cheap classification requests ``fast``, and the
analysis layer requests ``embedding``. The user maps roles to models once in
Settings.
"""

from __future__ import annotations

from typing import Any

from ..core.config import LLMProviderConfig, get_settings
from ..core.errors import ConfigurationError
from ..core.logging_setup import get_logger
from .backends import BACKENDS
from .base import LLMBackend

log = get_logger(__name__)

Role = str  # "chat" | "fast" | "embedding"


def provider_configs(*, enabled_only: bool = True) -> list[LLMProviderConfig]:
    providers = get_settings().llm.providers
    return [p for p in providers if p.enabled] if enabled_only else list(providers)


def get_provider_config(provider_id: str) -> LLMProviderConfig | None:
    for provider in get_settings().llm.providers:
        if provider.id == provider_id:
            return provider
    return None


def has_any_provider() -> bool:
    """True when at least one usable provider is configured.

    "Usable" means enabled and either holding an API key or being a local
    endpoint that needs none. Callers use this to decide whether to offer an
    LLM-backed path at all, instead of failing mid-operation.
    """
    for provider in provider_configs():
        if provider.kind == "ollama" or provider.api_key:
            return True
    return False


def build_backend(provider_id: str) -> LLMBackend:
    config = get_provider_config(provider_id)
    if config is None:
        raise ConfigurationError(
            f"LLM provider '{provider_id}' is not configured. Add it in "
            f"Settings > Models.",
            details={"configured": [p.id for p in get_settings().llm.providers]},
        )
    if not config.enabled:
        raise ConfigurationError(
            f"LLM provider '{provider_id}' is disabled",
            details={"provider": provider_id},
        )
    backend_class = BACKENDS.get(config.kind)
    if backend_class is None:
        raise ConfigurationError(
            f"unknown provider kind '{config.kind}' for '{provider_id}'. "
            f"Supported: {', '.join(sorted(BACKENDS))}",
            details={"provider": provider_id, "kind": config.kind},
        )
    if config.kind != "ollama" and not config.api_key:
        raise ConfigurationError(
            f"LLM provider '{provider_id}' has no API key. Set it in "
            f"Settings > Models.",
            details={"provider": provider_id},
        )
    return backend_class(config)


def default_for_role(role: Role) -> str:
    """Configured ``provider:model`` for a role, or ``""``."""
    settings = get_settings().llm
    return {
        "chat": settings.default_chat,
        "fast": settings.default_fast or settings.default_chat,
        "embedding": settings.default_embedding,
    }.get(role, settings.default_chat)


def resolve(spec: str = "", *, role: Role = "chat") -> tuple[LLMBackend, str]:
    """Resolve a model spec to ``(backend, model_name)``.

    Accepted forms, in order of precedence:

    * ``"provider:model"`` - explicit, always wins.
    * ``"provider"`` - that provider's ``default_model``.
    * ``"model"`` - the first configured provider that lists it, else the first
      usable provider (which lets a user type a model name the endpoint knows
      about but that is not in our list).
    * ``""`` - the role default, else the first usable provider.
    """
    candidate = (spec or default_for_role(role) or "").strip()
    providers = provider_configs()
    if not providers:
        raise ConfigurationError(
            "no LLM provider is configured. Add an API key in Settings > Models, "
            "or run a local model with Ollama.",
            details={"role": role},
        )

    if ":" in candidate:
        provider_id, _, model = candidate.partition(":")
        return build_backend(provider_id.strip()), model.strip()

    if candidate:
        exact = get_provider_config(candidate)
        if exact is not None:
            backend = build_backend(candidate)
            return backend, backend.resolve_model()
        for provider in providers:
            if candidate in provider.models or candidate == provider.default_model:
                return build_backend(provider.id), candidate
        # Treat it as a model name for the first usable provider.
        first = _first_usable(providers)
        log.info(
            "model '%s' is not listed for any provider; sending it to '%s'",
            candidate, first.id,
        )
        return build_backend(first.id), candidate

    first = _first_usable(providers)
    backend = build_backend(first.id)
    return backend, backend.resolve_model()


def _first_usable(providers: list[LLMProviderConfig]) -> LLMProviderConfig:
    for provider in providers:
        if provider.kind == "ollama" or provider.api_key:
            return provider
    raise ConfigurationError(
        "every configured LLM provider is missing its API key. Set one in "
        "Settings > Models.",
        details={"providers": [p.id for p in providers]},
    )


async def describe_all(*, probe: bool = False) -> list[dict[str, Any]]:
    """Provider catalogue for the settings UI.

    ``probe=True`` queries each endpoint for its model list, which is a live
    connectivity check. Failures are reported per provider rather than raising,
    so one unreachable endpoint does not blank the whole panel.
    """
    out: list[dict[str, Any]] = []
    for config in provider_configs(enabled_only=False):
        entry: dict[str, Any] = {
            "id": config.id,
            "kind": config.kind,
            "label": config.label or config.id,
            "base_url": config.base_url,
            "default_model": config.default_model,
            "models": list(config.models),
            "enabled": config.enabled,
            "has_key": bool(config.api_key),
            "needs_key": config.kind != "ollama",
            "timeout_s": config.timeout_s,
            "price_in_per_mtok": config.price_in_per_mtok,
            "price_out_per_mtok": config.price_out_per_mtok,
            "reachable": None,
            "error": "",
        }
        if probe:
            try:
                backend = build_backend(config.id)
                models = await backend.list_models()
                entry["models"] = models
                entry["reachable"] = True
            except Exception as exc:  # noqa: BLE001 - report, do not raise
                entry["reachable"] = False
                entry["error"] = str(exc)
        out.append(entry)
    return out


def role_defaults() -> dict[str, str]:
    settings = get_settings().llm
    return {
        "chat": settings.default_chat,
        "fast": settings.default_fast,
        "embedding": settings.default_embedding,
    }


def status() -> dict[str, Any]:
    """Compact status for the health endpoint and the UI status bar."""
    providers = provider_configs(enabled_only=False)
    usable = [
        p.id for p in providers
        if p.enabled and (p.kind == "ollama" or p.api_key)
    ]
    return {
        "configured": [p.id for p in providers],
        "usable": usable,
        "has_any": bool(usable),
        "roles": role_defaults(),
        "supported_kinds": sorted(BACKENDS),
    }

"""Layered configuration.

Resolution order (later wins), so that anything the user changes in the UI
survives a restart while CI / launcher environments can still force a value:

1. Field defaults declared on the models below.
2. ``<home>/config/settings.json`` - non-secret user settings written by the UI.
3. ``<home>/config/secrets.json`` - API keys written by the UI.
4. ``.env`` file - repo root when running from a checkout, else
   ``<home>/.env``. Parsed by :func:`load_dotenv_file`; never overwrites an
   already-set process env var.
5. Process environment (``PC_*`` and ``PAPERCREATOR_*``), which is the final
   authority so launchers and deterministic tests can force a value.

Two rules the rest of the codebase relies on:

* Secrets never appear in ``settings.json`` and never in a log record. The
  ``/api/settings`` endpoint returns them masked via :meth:`Settings.redacted`.
* :func:`get_settings` is cached. Anything that mutates config must call
  :func:`reload_settings` so long-lived services observe the change.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from .paths import find_repo_root, get_paths

# Populated by load_dotenv_file() so diagnostics can distinguish values injected
# from a dotenv file from values supplied by the launcher/process environment.
_dotenv_source: Path | None = None
_dotenv_values: dict[str, str] = {}
_dotenv_lock = threading.RLock()


def load_dotenv_file() -> Path | None:
    """Load ``.env`` into ``os.environ`` without clobbering existing values.

    Deliberately minimal (no python-dotenv dependency): ``KEY=value`` lines,
    ``#`` comments, optional ``export`` prefix, optional surrounding quotes.
    """
    global _dotenv_source, _dotenv_values
    with _dotenv_lock:
        # Values injected by an earlier call may be refreshed after a hand edit.
        # A value changed by the launcher/process is preserved as an override.
        for key, old_value in _dotenv_values.items():
            if os.environ.get(key) == old_value:
                os.environ.pop(key, None)
        _dotenv_values = {}
        _dotenv_source = None

        candidates: list[Path] = []
        repo = find_repo_root()
        if repo is not None:
            candidates.append(repo / ".env")
        candidates.append(get_paths().home / ".env")

        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                text = candidate.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            loaded: dict[str, str] = {}
            for raw in text.splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                key, sep, value = line.partition("=")
                if not sep:
                    continue
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
                    loaded[key] = value
            _dotenv_values = loaded
            _dotenv_source = candidate
            return candidate
        return None


def dotenv_source() -> str | None:
    return str(_dotenv_source) if _dotenv_source else None


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "INFO"
    cors_extra: list[str] = Field(default_factory=list)


class IdentitySettings(BaseModel):
    """Sent to scholarly APIs. Crossref/OpenAlex reward identified clients."""

    contact_email: str = ""
    user_agent_suffix: str = ""


class ProviderCredentials(BaseModel):
    """API keys for retrieval providers, keyed by provider id.

    Absent/blank means "unauthenticated". Providers decide whether they can
    still run (arXiv: yes; IEEE: no) via ``Provider.availability()``.
    """

    openalex: str = ""
    semanticscholar: str = ""
    ncbi: str = ""
    core: str = ""
    springer: str = ""
    ieee: str = ""
    scopus: str = ""

    def get(self, provider_id: str) -> str:
        return str(getattr(self, provider_id, "") or "")


class LLMProviderConfig(BaseModel):
    """One configured LLM endpoint.

    ``kind`` selects the wire protocol implementation in ``papercreator.llm``:
    ``openai`` covers OpenAI plus every compatible gateway (DeepSeek,
    OpenRouter, vLLM, LM Studio, Groq...), which is why only four kinds are
    needed to reach most models.
    """

    id: str
    kind: Literal["openai", "anthropic", "gemini", "ollama"] = "openai"
    label: str = ""
    base_url: str = ""
    api_key: str = ""
    default_model: str = ""
    models: list[str] = Field(default_factory=list)
    enabled: bool = True
    timeout_s: float = 300.0
    max_retries: int = 2
    # Cost bookkeeping only; never used for routing decisions.
    price_in_per_mtok: float = 0.0
    price_out_per_mtok: float = 0.0


class LLMSettings(BaseModel):
    providers: list[LLMProviderConfig] = Field(default_factory=list)
    # "provider_id:model" - resolved by llm.registry.resolve()
    default_chat: str = ""
    default_fast: str = ""
    default_embedding: str = ""
    temperature: float = 0.4
    max_output_tokens: int = 4096
    # Hard ceiling per agent run; the orchestrator aborts past this.
    run_token_budget: int = 400_000


class RetrievalSettings(BaseModel):
    enabled_providers: list[str] = Field(
        default_factory=lambda: ["arxiv", "openalex", "crossref"]
    )
    default_limit_per_provider: int = 50
    total_limit: int = 300
    http_timeout_s: float = 45.0
    cache_ttl_hours: int = 168  # one week; scholarly metadata is near-static
    use_cache: bool = True
    # Advanced deployments may route OpenAlex through an HTTPS mirror/proxy.
    # Plain HTTP is accepted only on loopback, which keeps local development
    # and deterministic E2E tests possible without leaking an API key over the
    # network.
    openalex_endpoint: str = "https://api.openalex.org/works"
    # Minimum title similarity for two records to be merged. See
    # retrieval/dedupe.py; 0.90 tolerates subtitle/casing drift without
    # merging distinct papers that share a prefix.
    dedupe_title_threshold: float = 0.90
    download_oa_pdfs: bool = False

    @field_validator("openalex_endpoint")
    @classmethod
    def _safe_openalex_endpoint(cls, value: str) -> str:
        endpoint = str(value or "").strip().rstrip("/")
        parsed = urlsplit(endpoint)
        if parsed.scheme == "https" and parsed.hostname:
            return endpoint
        if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
            return endpoint
        raise ValueError(
            "openalex_endpoint must use HTTPS; HTTP is allowed only for a loopback host"
        )


class AnalysisSettings(BaseModel):
    embedding_backend: Literal["auto", "sentence-transformers", "llm", "tfidf", "hashing"] = "auto"
    sentence_transformer_model: str = "all-MiniLM-L6-v2"
    # Mirror for Hugging Face model downloads. huggingface.co is unreachable from
    # some networks (verified on this machine); https://hf-mirror.com serves the
    # same artefacts and is the common workaround. Exported as HF_ENDPOINT before
    # any transformers import - see analysis/embeddings.py.
    hf_endpoint: str = ""
    # Never attempt a download; use only models already in the local cache.
    offline_models: bool = False
    reducer: Literal["auto", "umap", "pca", "tsne", "mds"] = "auto"
    clusterer: Literal["auto", "hdbscan", "kmeans", "agglomerative"] = "auto"
    n_neighbors: int = 15
    min_dist: float = 0.1
    min_cluster_size: int = 5
    keyword_top_k: int = 12
    heatmap_grid: int = 40
    random_state: int = 42


class WritingSettings(BaseModel):
    default_language: Literal["en", "zh"] = "en"
    bilingual: bool = True
    citation_style: str = "ieee"
    latex_engine: Literal["pdflatex", "xelatex", "lualatex"] = "pdflatex"
    auto_git_commit: bool = True


class AssistantSettings(BaseModel):
    # Zero disables the retention policy. The policy is never executed
    # automatically; it only supplies the explicit maintenance preview.
    retention_days: int = Field(default=0, ge=0, le=3650)


class UISettings(BaseModel):
    theme: Literal["dark", "light"] = "dark"
    accent: str = "#4f9cf9"
    font_size: int = 13
    sidebar_width: int = 300
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    # Version of the task-based quick start the user has explicitly dismissed.
    # A versioned integer lets a future, materially different guide be offered
    # once without maintaining migration-only booleans.
    quick_start_version: int = Field(default=0, ge=0)


class OverleafSettings(BaseModel):
    git_url: str = ""
    git_token: str = ""


class Settings(BaseModel):
    server: ServerSettings = Field(default_factory=ServerSettings)
    identity: IdentitySettings = Field(default_factory=IdentitySettings)
    provider_keys: ProviderCredentials = Field(default_factory=ProviderCredentials)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    analysis: AnalysisSettings = Field(default_factory=AnalysisSettings)
    writing: WritingSettings = Field(default_factory=WritingSettings)
    assistant: AssistantSettings = Field(default_factory=AssistantSettings)
    ui: UISettings = Field(default_factory=UISettings)
    overleaf: OverleafSettings = Field(default_factory=OverleafSettings)

    # ------------------------------------------------------------ helpers
    def user_agent(self) -> str:
        """Identify ourselves to scholarly APIs, per their terms of use."""
        base = "PaperCreator/0.1"
        email = self.identity.contact_email.strip()
        if email:
            base += f" (mailto:{email})"
        suffix = self.identity.user_agent_suffix.strip()
        if suffix:
            base += f" {suffix}"
        return base

    def redacted(self) -> dict[str, Any]:
        """Deep copy with every secret replaced by a presence marker.

        ``""`` means unset, ``"***set***"`` means configured. The UI shows a
        filled placeholder for the latter and sends back the marker unchanged
        when the user did not retype the key (see settings route).
        """
        data = self.model_dump(mode="json")
        for key in list(data.get("provider_keys", {})):
            if data["provider_keys"][key]:
                data["provider_keys"][key] = MASK
        for provider in data.get("llm", {}).get("providers", []):
            if provider.get("api_key"):
                provider["api_key"] = MASK
        if data.get("overleaf", {}).get("git_token"):
            data["overleaf"]["git_token"] = MASK
        return data


MASK = "***set***"

# ---------------------------------------------------------------------------
# Layer assembly
# ---------------------------------------------------------------------------


def _from_env() -> dict[str, Any]:
    """Environment layer. Only keys actually present are included."""
    data: dict[str, Any] = {}

    def put(section: str, field: str, env_name: str, convert=lambda value: value) -> None:
        if env_name not in os.environ:
            return
        raw = os.environ[env_name]
        data.setdefault(section, {})[field] = convert(raw)

    if os.environ.get("PC_HOST", "").strip():
        put("server", "host", "PC_HOST")
    if os.environ.get("PC_PORT", "").strip():
        put("server", "port", "PC_PORT", lambda value: _env_int("PC_PORT", 8765))
    if os.environ.get("PC_LOG_LEVEL", "").strip():
        put("server", "log_level", "PC_LOG_LEVEL", lambda value: value.upper())
    put(
        "server", "cors_extra", "PC_CORS_EXTRA",
        lambda value: [item.strip() for item in value.split(",") if item.strip()],
    )
    put("identity", "contact_email", "PC_CONTACT_EMAIL")
    put("analysis", "offline_models", "PC_OFFLINE_MODELS", lambda value: _env_bool("PC_OFFLINE_MODELS", False))
    if "PC_HF_ENDPOINT" in os.environ:
        put("analysis", "hf_endpoint", "PC_HF_ENDPOINT")
    elif "HF_ENDPOINT" in os.environ:
        put("analysis", "hf_endpoint", "HF_ENDPOINT")
    if os.environ.get("PC_OPENALEX_ENDPOINT", "").strip():
        put("retrieval", "openalex_endpoint", "PC_OPENALEX_ENDPOINT")

    for env_name, field in (
        ("PC_OPENALEX_API_KEY", "openalex"),
        ("PC_S2_API_KEY", "semanticscholar"),
        ("PC_NCBI_API_KEY", "ncbi"),
        ("PC_CORE_API_KEY", "core"),
        ("PC_SPRINGER_API_KEY", "springer"),
        ("PC_IEEE_API_KEY", "ieee"),
        ("PC_SCOPUS_API_KEY", "scopus"),
    ):
        put("provider_keys", field, env_name)
    put("overleaf", "git_url", "PC_OVERLEAF_GIT_URL")
    put("overleaf", "git_token", "PC_OVERLEAF_GIT_TOKEN")
    providers = _llm_providers_from_env()
    if providers:
        data["llm"] = {"providers": providers}
    return data


# (env key, provider id, human label, kind, default base url, default model)
_LLM_ENV_SPECS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("PC_OPENAI_API_KEY", "openai", "OpenAI", "openai",
     "https://api.openai.com/v1", "gpt-4o-mini"),
    ("PC_ANTHROPIC_API_KEY", "anthropic", "Anthropic", "anthropic",
     "https://api.anthropic.com", "claude-sonnet-4-5"),
    ("PC_GEMINI_API_KEY", "gemini", "Google Gemini", "gemini",
     "https://generativelanguage.googleapis.com", "gemini-2.0-flash"),
    ("PC_DEEPSEEK_API_KEY", "deepseek", "DeepSeek", "openai",
     "https://api.deepseek.com/v1", "deepseek-chat"),
    ("PC_OPENROUTER_API_KEY", "openrouter", "OpenRouter", "openai",
     "https://openrouter.ai/api/v1", "openai/gpt-4o-mini"),
)

_SUPPORTED_ENV_NAMES: frozenset[str] = frozenset({
    "PC_HOST", "PC_PORT", "PC_LOG_LEVEL", "PC_CORS_EXTRA",
    "PC_CONTACT_EMAIL", "PC_HF_ENDPOINT", "HF_ENDPOINT",
    "PC_OFFLINE_MODELS", "PC_OPENALEX_ENDPOINT",
    "PC_OPENALEX_API_KEY", "PC_S2_API_KEY", "PC_NCBI_API_KEY",
    "PC_CORE_API_KEY", "PC_SPRINGER_API_KEY", "PC_IEEE_API_KEY",
    "PC_SCOPUS_API_KEY", "PC_OVERLEAF_GIT_URL", "PC_OVERLEAF_GIT_TOKEN",
    "PC_OLLAMA_BASE_URL", "PC_OLLAMA_MODEL",
    *[spec[0] for spec in _LLM_ENV_SPECS],
    *[f"PC_{spec[1].upper()}_BASE_URL" for spec in _LLM_ENV_SPECS],
})

_DIRECT_ENV_FIELDS: dict[str, str] = {
    "PC_HOST": "server.host",
    "PC_PORT": "server.port",
    "PC_LOG_LEVEL": "server.log_level",
    "PC_CORS_EXTRA": "server.cors_extra",
    "PC_CONTACT_EMAIL": "identity.contact_email",
    "PC_HF_ENDPOINT": "analysis.hf_endpoint",
    "HF_ENDPOINT": "analysis.hf_endpoint",
    "PC_OFFLINE_MODELS": "analysis.offline_models",
    "PC_OPENALEX_ENDPOINT": "retrieval.openalex_endpoint",
    "PC_OPENALEX_API_KEY": "provider_keys.openalex",
    "PC_S2_API_KEY": "provider_keys.semanticscholar",
    "PC_NCBI_API_KEY": "provider_keys.ncbi",
    "PC_CORE_API_KEY": "provider_keys.core",
    "PC_SPRINGER_API_KEY": "provider_keys.springer",
    "PC_IEEE_API_KEY": "provider_keys.ieee",
    "PC_SCOPUS_API_KEY": "provider_keys.scopus",
    "PC_OVERLEAF_GIT_URL": "overleaf.git_url",
    "PC_OVERLEAF_GIT_TOKEN": "overleaf.git_token",
    "PC_OLLAMA_BASE_URL": "llm.providers.ollama",
    "PC_OLLAMA_MODEL": "llm.providers.ollama",
    **{spec[0]: f"llm.providers.{spec[1]}" for spec in _LLM_ENV_SPECS},
    **{
        f"PC_{spec[1].upper()}_BASE_URL": f"llm.providers.{spec[1]}"
        for spec in _LLM_ENV_SPECS
    },
}


def _field_paths_for_variables(names: list[str]) -> list[str]:
    paths: set[str] = set()
    for name in names:
        path = _DIRECT_ENV_FIELDS.get(name)
        if path:
            paths.add(path)
    # PC_HF_ENDPOINT, even when blank, takes precedence over HF_ENDPOINT.
    if "PC_HF_ENDPOINT" in names:
        paths.add("analysis.hf_endpoint")
    return sorted(paths)


def _llm_providers_from_env() -> list[dict[str, Any]]:
    """Auto-register a provider for each API key found in the environment.

    Ollama is registered whenever a base URL is configured because it needs no
    key; reachability is checked lazily at call time, not here.
    """
    out: list[dict[str, Any]] = []
    for env_key, pid, label, kind, base, model in _LLM_ENV_SPECS:
        key = _env(env_key, "") or ""
        if not key:
            continue
        url_override = _env(f"PC_{pid.upper()}_BASE_URL", "") or ""
        out.append({
            "id": pid, "kind": kind, "label": label,
            "base_url": url_override or base,
            "api_key": key, "default_model": model, "models": [model],
        })
    ollama_url = _env("PC_OLLAMA_BASE_URL", "") or ""
    if ollama_url:
        out.append({
            "id": "ollama", "kind": "ollama", "label": "Ollama (local)",
            "base_url": ollama_url, "api_key": "",
            "default_model": _env("PC_OLLAMA_MODEL", "") or "",
            "timeout_s": 600.0,
        })
    return out


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A corrupt settings file must not stop the app from booting; the
        # health endpoint surfaces the problem instead.
        return {}
    return data if isinstance(data, dict) else {}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive dict merge. Lists replace wholesale except LLM providers,
    which merge by ``id`` so a UI edit of one provider cannot drop the others.
    """
    out = dict(base)
    for key, value in overlay.items():
        if key == "providers" and isinstance(value, list) and isinstance(out.get(key), list):
            out[key] = _merge_providers(out[key], value)
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        elif value is not None:
            out[key] = value
        elif key not in out:
            out[key] = value
    return out


def _merge_providers(
    base: list[dict[str, Any]], overlay: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    index = {p.get("id"): dict(p) for p in base if isinstance(p, dict)}
    order = [p.get("id") for p in base if isinstance(p, dict)]
    for item in overlay:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        pid = item["id"]
        if pid in index:
            index[pid] = _deep_merge(index[pid], item)
        else:
            index[pid] = dict(item)
            order.append(pid)
    return [index[pid] for pid in order if pid in index]


_lock = threading.RLock()
_cached: Settings | None = None


def build_settings() -> Settings:
    """Assemble all layers into a fresh :class:`Settings`."""
    paths = get_paths()
    data: dict[str, Any] = {}
    data = _deep_merge(data, _read_json(paths.settings_file))
    data = _deep_merge(data, _read_json(paths.secrets_file))
    # Launcher/process values are the final authority. This makes deployments
    # reproducible and lets deterministic tests block a developer's local keys.
    data = _deep_merge(data, _from_env())
    return Settings.model_validate(data)


def _flatten_fields(data: Any, prefix: str = "") -> list[str]:
    if isinstance(data, dict):
        fields: list[str] = []
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            fields.extend(_flatten_fields(value, path))
        return fields
    if isinstance(data, list):
        return [prefix] if prefix else []
    return [prefix] if prefix else []


def configuration_sources() -> dict[str, Any]:
    """Describe configuration provenance without returning configuration values."""
    paths = get_paths()
    settings_data = _read_json(paths.settings_file)
    secrets_data = _read_json(paths.secrets_file)
    with _dotenv_lock:
        dotenv_names = sorted(
            key for key, value in _dotenv_values.items()
            if os.environ.get(key) == value
        )
    env_names = sorted(
        name for name in _SUPPORTED_ENV_NAMES
        if name in os.environ and name not in dotenv_names
    )
    return {
        "precedence": ["defaults", "settings_file", "secrets_file", "dotenv", "environment"],
        "settings_file": {
            "path": str(paths.settings_file),
            "exists": paths.settings_file.is_file(),
            "fields": sorted(_flatten_fields(settings_data)),
        },
        "secrets_file": {
            "path": str(paths.secrets_file),
            "exists": paths.secrets_file.is_file(),
            "fields": sorted(_flatten_fields(secrets_data)),
        },
        "dotenv": {
            "path": dotenv_source(),
            "variables": dotenv_names,
            "override_fields": _field_paths_for_variables(dotenv_names),
        },
        "environment": {
            "variables": env_names,
            "override_fields": _field_paths_for_variables(env_names),
        },
    }


def get_settings() -> Settings:
    global _cached
    with _lock:
        if _cached is None:
            _cached = build_settings()
        return _cached


def reload_settings() -> Settings:
    """Rebuild from disk + env. Call after any config write."""
    global _cached
    with _lock:
        _cached = build_settings()
        return _cached

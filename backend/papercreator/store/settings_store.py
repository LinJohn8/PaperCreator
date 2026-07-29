"""Persisting user settings and secrets.

Split into two files on purpose:

``config/settings.json``
    Non-secret. Safe to sync, back up, paste into a bug report.

``config/secrets.json``
    API keys only. ``0600`` on POSIX. Excluded from exports and never logged.

Writes are atomic (temp file + ``os.replace``) because a half-written
settings.json would break the next boot, and the app writes on every settings
change from the UI.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from ..core.config import MASK, get_settings, reload_settings
from ..core.db import execute, query_one
from ..core.logging_setup import get_logger
from ..core.paths import get_paths
from ..core.util import utc_now_iso

log = get_logger(__name__)

# Dotted paths that hold secrets. Routed to secrets.json, stripped from
# settings.json, and masked in API responses.
SECRET_PATHS: tuple[str, ...] = (
    "provider_keys.openalex",
    "provider_keys.semanticscholar",
    "provider_keys.ncbi",
    "provider_keys.core",
    "provider_keys.springer",
    "provider_keys.ieee",
    "provider_keys.scopus",
    "overleaf.git_token",
)


def _atomic_write_json(path: Path, data: dict[str, Any], *, secret: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), delete=False, suffix=".tmp"
    )
    try:
        with handle as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        temp_path = Path(handle.name)
        if secret and os.name != "nt":
            # Windows has no meaningful equivalent; ACLs on %APPDATA% already
            # restrict to the user.
            temp_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temp_path, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def read_settings_file() -> dict[str, Any]:
    path = get_paths().settings_file
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        log.error("settings.json is unreadable (%s); using defaults", exc)
        return {}


def read_secrets_file() -> dict[str, Any]:
    path = get_paths().secrets_file
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        log.error("secrets.json is unreadable (%s); keys unavailable", exc)
        return {}


def _get_path(data: dict[str, Any], dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _set_path(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            return
    node[parts[-1]] = value


def _pop_path(data: dict[str, Any], dotted: str) -> Any:
    parts = dotted.split(".")
    node = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if isinstance(node, dict):
        return node.pop(parts[-1], None)
    return None


def _split_secrets(patch: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Route a settings patch into (public, secret) halves.

    A value equal to :data:`MASK` means "unchanged" - the UI echoes back the
    mask for keys the user did not retype, and rewriting the mask as the key
    would destroy the real one.
    """
    public = json.loads(json.dumps(patch))  # deep copy, JSON-safe
    secrets: dict[str, Any] = {}
    for dotted in SECRET_PATHS:
        value = _pop_path(public, dotted)
        if value is None:
            continue
        if value == MASK:
            continue
        _set_path(secrets, dotted, value)

    # LLM provider api_keys live inside a list; handle separately.
    providers = _get_path(public, "llm.providers")
    if isinstance(providers, list):
        secret_providers = []
        for entry in providers:
            if not isinstance(entry, dict):
                continue
            key = entry.get("api_key")
            if key and key != MASK:
                secret_providers.append({"id": entry.get("id"), "api_key": key})
            if "api_key" in entry:
                entry.pop("api_key")
        if secret_providers:
            _set_path(secrets, "llm.providers", secret_providers)
    return public, secrets


def _deep_update(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if key == "providers" and isinstance(value, list) and isinstance(
            base.get(key), list
        ):
            index = {p.get("id"): dict(p) for p in base[key] if isinstance(p, dict)}
            order = [p.get("id") for p in base[key] if isinstance(p, dict)]
            for item in value:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                pid = item["id"]
                if pid in index:
                    index[pid] = _deep_update(index[pid], item)
                else:
                    index[pid] = dict(item)
                    order.append(pid)
            base[key] = [index[p] for p in order if p in index]
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a partial settings update into disk, then reload the cache.

    Returns the redacted effective settings, ready to send to the client.
    """
    public_patch, secret_patch = _split_secrets(patch)

    if public_patch:
        current = read_settings_file()
        _atomic_write_json(
            get_paths().settings_file, _deep_update(current, public_patch)
        )
    if secret_patch:
        current_secrets = read_secrets_file()
        _atomic_write_json(
            get_paths().secrets_file,
            _deep_update(current_secrets, secret_patch),
            secret=True,
        )
    settings = reload_settings()
    log.info(
        "settings updated (public keys: %s, secret keys: %s)",
        sorted(public_patch.keys()), sorted(secret_patch.keys()),
    )
    return settings.redacted()


def delete_secret(dotted: str) -> dict[str, Any]:
    """Remove one stored key (the UI's "clear" button)."""
    secrets = read_secrets_file()
    _pop_path(secrets, dotted)
    _atomic_write_json(get_paths().secrets_file, secrets, secret=True)
    return reload_settings().redacted()


def effective_settings() -> dict[str, Any]:
    return get_settings().redacted()


# ------------------------------------------------------------- app_state kv


def get_state(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value FROM app_state WHERE key=?", (key,))
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return row["value"]


def set_state(key: str, value: Any) -> None:
    execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?,?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
        " updated_at=excluded.updated_at",
        (key, json.dumps(value, ensure_ascii=False), utc_now_iso()),
    )

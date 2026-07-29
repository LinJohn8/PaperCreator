"""Typed application errors.

Every error the API deliberately surfaces derives from :class:`AppError`, which
carries an HTTP status, a stable machine ``code`` for the frontend to branch
on, and an optional ``details`` payload. The exception handler in
``api/app.py`` turns these into::

    {"error": {"code": "provider_unavailable", "message": "...", "details": {...}}}

Anything *not* deriving from ``AppError`` is a bug: the handler logs it with a
traceback and returns a generic 500 without leaking internals.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "app_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code

    def to_payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"


class ConflictError(AppError):
    """Requested state change conflicts with current state (duplicate slug,
    dirty git tree, job already running)."""

    status_code = 409
    code = "conflict"


class ConfigurationError(AppError):
    """A required setting is missing or malformed - e.g. no LLM provider
    configured while an agent run was requested. Actionable by the user in
    Settings, so the message should name the setting."""

    status_code = 400
    code = "configuration_error"


class ProviderError(AppError):
    """A retrieval provider failed. Non-fatal by design: the search pipeline
    records it per-provider and returns whatever the others found."""

    status_code = 502
    code = "provider_error"


class ProviderUnavailableError(ProviderError):
    """Provider cannot run at all (missing key, disabled). Distinct from
    ``ProviderError`` so the UI can offer a "configure key" action."""

    status_code = 400
    code = "provider_unavailable"


class RateLimitError(ProviderError):
    status_code = 429
    code = "rate_limited"


class LLMError(AppError):
    status_code = 502
    code = "llm_error"


_LLM_RETRYABLE_OUTCOMES = {
    "rate_limited",
    "timeout",
    "network_error",
    "stream_interrupted",
}


def llm_error(
    message: str,
    *,
    outcome: str,
    provider: str = "",
    model: str = "",
    retryable: bool | None = None,
    http_status: int | None = None,
    retry_after_s: float | None = None,
    hint: str = "",
    details: dict[str, Any] | None = None,
) -> LLMError:
    """Build an :class:`LLMError` with the stable recovery contract.

    The same fields are persisted on usage rows' surrounding run/step records,
    emitted over SSE, and rendered by the desktop application.  Keeping the
    construction here prevents four wire-protocol adapters from inventing
    subtly different names for the same failure.
    """
    merged = dict(details or {})
    merged.update(
        {
            "outcome": outcome,
            "error_code": f"llm_{outcome}",
            "retryable": (
                outcome in _LLM_RETRYABLE_OUTCOMES
                if retryable is None
                else bool(retryable)
            ),
            "http_status": http_status,
            "retry_after_s": retry_after_s,
            "hint": hint,
            "provider": provider or str(merged.get("provider") or ""),
            "model": model or str(merged.get("model") or ""),
        }
    )
    status_code = 429 if outcome == "rate_limited" else 502
    if outcome in {"authentication_error", "configuration_error", "unavailable"}:
        status_code = 400
    return LLMError(
        message,
        details=merged,
        code=f"llm_{outcome}",
        status_code=status_code,
    )


def error_diagnostics(
    exc: BaseException,
    *,
    provider: str = "",
    model: str = "",
) -> dict[str, Any]:
    """Return a JSON-safe, UI-facing diagnosis for any boundary exception."""
    details = dict(getattr(exc, "details", {}) or {})
    if isinstance(exc, LLMError):
        outcome = str(details.get("outcome") or "model_error")
        error_code = str(details.get("error_code") or getattr(exc, "code", "llm_error"))
        retryable = bool(
            details.get("retryable", outcome in _LLM_RETRYABLE_OUTCOMES)
        )
    elif isinstance(exc, ConfigurationError):
        outcome = "configuration_error"
        error_code = str(getattr(exc, "code", "configuration_error"))
        retryable = False
        details.setdefault(
            "hint", "Open Settings > Models and correct the provider configuration."
        )
    elif isinstance(exc, CancelledError):
        outcome = "cancelled"
        error_code = str(getattr(exc, "code", "cancelled"))
        retryable = False
    else:
        outcome = str(details.get("outcome") or "unexpected_error")
        error_code = str(
            details.get("error_code")
            or getattr(exc, "code", "llm_unexpected_error")
        )
        retryable = bool(details.get("retryable", False))

    diagnostic: dict[str, Any] = {
        "outcome": outcome,
        "error_code": error_code,
        "retryable": retryable,
        "http_status": details.get("http_status"),
        "retry_after_s": details.get("retry_after_s"),
        "hint": str(details.get("hint") or ""),
        "provider": provider or str(details.get("provider") or ""),
        "model": model or str(details.get("model") or ""),
        "message": str(getattr(exc, "message", "") or str(exc)),
        "error_type": type(exc).__name__,
    }
    for key in (
        "partial_output",
        "partial_output_chars",
        "partial_tokens_in",
        "partial_tokens_out",
        "finish_reason",
        "max_tokens",
    ):
        if key in details:
            diagnostic[key] = details[key]
    return diagnostic


def enrich_llm_error(
    exc: LLMError,
    *,
    provider: str = "",
    model: str = "",
    **details: Any,
) -> LLMError:
    """Attach call/partial-output context without replacing the root cause."""
    if provider:
        exc.details["provider"] = provider
    if model:
        exc.details["model"] = model
    for key, value in details.items():
        if value is not None:
            exc.details[key] = value
    exc.details.setdefault("outcome", "model_error")
    exc.details.setdefault("error_code", getattr(exc, "code", "llm_error"))
    exc.details.setdefault(
        "retryable", exc.details["outcome"] in _LLM_RETRYABLE_OUTCOMES
    )
    exc.details.setdefault("http_status", None)
    exc.details.setdefault("retry_after_s", None)
    exc.details.setdefault("hint", "")
    return exc


class DependencyMissingError(AppError):
    """An optional Python package is needed for this operation.

    ``details["package"]`` and ``details["extra"]`` tell the UI exactly what to
    install, e.g. ``pip install "papercreator[analysis]"``.
    """

    status_code = 400
    code = "dependency_missing"

    def __init__(self, package: str, *, extra: str = "", purpose: str = "") -> None:
        hint = f'pip install "papercreator[{extra}]"' if extra else f"pip install {package}"
        message = f"Optional dependency '{package}' is required"
        if purpose:
            message += f" for {purpose}"
        message += f". Install with: {hint}"
        super().__init__(
            message, details={"package": package, "extra": extra, "hint": hint}
        )


class ExternalToolError(AppError):
    """A CLI we shell out to (git, pandoc, latexmk) failed or is absent."""

    status_code = 500
    code = "external_tool_error"


class CancelledError(AppError):
    status_code = 499
    code = "cancelled"

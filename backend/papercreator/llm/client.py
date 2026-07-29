"""High-level LLM entry points used by agents, retrieval and analysis.

Everything above this layer calls these functions rather than a backend directly,
because they add the things every call needs:

* **model resolution** by role (``chat`` / ``fast`` / ``embedding``),
* **usage accounting** - every call writes an ``llm_usage`` row, so the cost of a
  paper is always answerable,
* **JSON repair** - :func:`complete_json` handles the ways models break strict
  JSON (fences, prose preambles, trailing commas, truncation) instead of every
  caller writing its own parser,
* **budget enforcement** - a run cannot silently consume unlimited tokens.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from ..core.config import get_settings
from ..core.errors import (
    LLMError,
    enrich_llm_error,
    error_diagnostics,
    llm_error,
)
from ..core.logging_setup import get_logger
from ..store import runs as runs_store
from . import registry
from .base import Completion, Message, StreamChunk

log = get_logger(__name__)


def _messages(prompt: str, system: str = "", history: list[Message] | None = None
              ) -> list[Message]:
    messages: list[Message] = []
    if system.strip():
        messages.append(Message(role="system", content=system))
    messages.extend(history or [])
    if prompt.strip():
        messages.append(Message(role="user", content=prompt))
    return messages


def _record(
    completion: Completion,
    *,
    backend: Any,
    purpose: str,
    run_id: str,
    ok: bool = True,
) -> None:
    cost = backend.estimate_cost(completion.usage)
    usage_id = runs_store.record_usage(
        provider=completion.provider,
        model=completion.model,
        purpose=purpose,
        run_id=run_id,
        tokens_in=completion.usage.prompt_tokens,
        tokens_out=completion.usage.completion_tokens,
        cost_usd=cost,
        duration_ms=completion.duration_ms,
        ok=ok,
    )
    completion.raw["_usage_record_id"] = usage_id
    if run_id:
        runs_store.add_run_usage(
            run_id,
            completion.usage.prompt_tokens,
            completion.usage.completion_tokens,
            cost,
        )


async def complete(
    prompt: str,
    *,
    system: str = "",
    history: list[Message] | None = None,
    model: str = "",
    role: str = "chat",
    temperature: float | None = None,
    max_tokens: int | None = None,
    stop: list[str] | None = None,
    json_mode: bool = False,
    purpose: str = "",
    run_id: str = "",
) -> Completion:
    """One completion with accounting. Raises :class:`LLMError` on failure."""
    settings = get_settings().llm
    backend, resolved_model = registry.resolve(model, role=role)
    started = time.perf_counter()
    try:
        completion = await backend.complete(
            _messages(prompt, system, history),
            model=resolved_model,
            temperature=settings.temperature if temperature is None else temperature,
            max_tokens=settings.max_output_tokens if max_tokens is None else max_tokens,
            stop=stop,
            json_mode=json_mode,
        )
    except Exception as exc:  # one failed ledger row for every attempted call
        runs_store.record_usage(
            provider=backend.provider_id, model=resolved_model, purpose=purpose,
            run_id=run_id, duration_ms=int((time.perf_counter() - started) * 1000),
            ok=False,
        )
        if isinstance(exc, LLMError):
            raise enrich_llm_error(
                exc, provider=backend.provider_id, model=resolved_model
            )
        raise llm_error(
            f"provider '{backend.provider_id}' failed unexpectedly: {exc}",
            outcome="unexpected_error", provider=backend.provider_id,
            model=resolved_model, retryable=False,
            hint="Review the failed step audit and application error log.",
            details={"cause_type": type(exc).__name__},
        ) from exc
    _record(completion, backend=backend, purpose=purpose, run_id=run_id)
    return completion


async def complete_text(prompt: str, **kwargs: Any) -> str:
    """Convenience wrapper returning just the text."""
    return (await complete(prompt, **kwargs)).text


async def stream(
    prompt: str,
    *,
    system: str = "",
    history: list[Message] | None = None,
    model: str = "",
    role: str = "chat",
    temperature: float | None = None,
    max_tokens: int | None = None,
    stop: list[str] | None = None,
    purpose: str = "",
    run_id: str = "",
) -> AsyncIterator[StreamChunk]:
    """Stream a completion with success/failure and partial-output accounting."""
    settings = get_settings().llm
    backend, resolved_model = registry.resolve(model, role=role)
    request_messages = _messages(prompt, system, history)
    started = time.perf_counter()
    partial: list[str] = []
    terminal = False

    def record_partial_failure(text: str) -> tuple[int, int]:
        usage = backend._estimate_usage(request_messages, text)
        _record(
            Completion(
                text=text,
                model=resolved_model,
                provider=backend.provider_id,
                usage=usage,
                duration_ms=int((time.perf_counter() - started) * 1000),
            ),
            backend=backend,
            purpose=purpose,
            run_id=run_id,
            ok=False,
        )
        return usage.prompt_tokens, usage.completion_tokens

    try:
        async for chunk in backend.stream(
            request_messages,
            model=resolved_model,
            temperature=settings.temperature if temperature is None else temperature,
            max_tokens=settings.max_output_tokens if max_tokens is None else max_tokens,
            stop=stop,
        ):
            if chunk.delta:
                partial.append(chunk.delta)
            if chunk.done:
                if chunk.completion is None:
                    raise llm_error(
                        f"provider '{backend.provider_id}' ended its stream without a completion",
                        outcome="invalid_response", provider=backend.provider_id,
                        model=resolved_model, retryable=False,
                    )
                terminal = True
                _record(
                    chunk.completion, backend=backend, purpose=purpose, run_id=run_id
                )
            yield chunk
        if not terminal:
            raise llm_error(
                f"provider '{backend.provider_id}' stream ended without a terminal event",
                outcome="stream_interrupted", provider=backend.provider_id,
                model=resolved_model, retryable=True,
                hint="Retry the run; partial text is available in the failed step audit.",
            )
    except Exception as exc:
        text = "".join(partial)
        if isinstance(exc, LLMError):
            existing = str(exc.details.get("partial_output") or "")
            if len(existing) > len(text):
                text = existing
            failure = enrich_llm_error(
                exc,
                provider=backend.provider_id,
                model=resolved_model,
                partial_output=text,
                partial_output_chars=len(text),
            )
        else:
            failure = llm_error(
                f"provider '{backend.provider_id}' stream failed unexpectedly: {exc}",
                outcome="unexpected_error", provider=backend.provider_id,
                model=resolved_model, retryable=False,
                hint="Review the failed step audit and application error log.",
                details={"cause_type": type(exc).__name__,
                         "partial_output": text,
                         "partial_output_chars": len(text)},
            )
        tokens_in, tokens_out = record_partial_failure(text)
        failure.details.setdefault("partial_tokens_in", tokens_in)
        failure.details.setdefault("partial_tokens_out", tokens_out)
        raise failure
    except (GeneratorExit, asyncio.CancelledError):
        # The consumer can close the generator after cooperative cancellation.
        # The provider call still happened and belongs in the usage ledger.
        if not terminal:
            record_partial_failure("".join(partial))
        raise


# --------------------------------------------------------------- JSON handling

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def extract_json(text: str) -> Any:
    """Parse JSON out of a model response, repairing the usual damage.

    Models break strict JSON in a small number of predictable ways, and each is
    cheap to fix here rather than in every caller:

    1. wrapped in a ``` fence,
    2. preceded by prose ("Here is the JSON:"),
    3. trailing commas before ``}``/``]``,
    4. truncated because the output hit the token limit - the outermost braces are
       balanced by counting, which recovers a usable partial object.

    Raises :class:`LLMError` with a snippet when nothing parses, so the caller can
    report something more useful than "invalid JSON".
    """
    if not text or not text.strip():
        raise llm_error(
            "model returned an empty response where JSON was expected",
            outcome="empty_response", retryable=True,
            hint="Retry once; if it repeats, select a model that supports JSON output.",
        )

    candidate = _FENCE.sub("", text.strip())
    try:
        return json.loads(candidate)
    except ValueError:
        pass

    # Find the outermost JSON value in the text.
    start = min(
        (i for i in (candidate.find("{"), candidate.find("[")) if i >= 0),
        default=-1,
    )
    if start < 0:
        raise llm_error(
            "model response contains no JSON object or array",
            outcome="invalid_response", retryable=False,
            hint="Use a model with reliable structured-output support.",
            details={"snippet": text[:400]},
        )
    opening = candidate[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    end = -1
    for index in range(start, len(candidate)):
        char = candidate[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    fragment = candidate[start:end] if end > start else candidate[start:]

    for attempt in (
        fragment,
        _TRAILING_COMMA.sub(r"\1", fragment),
        # Truncated output: close whatever is still open.
        _close_unbalanced(_TRAILING_COMMA.sub(r"\1", fragment)),
    ):
        try:
            return json.loads(attempt)
        except ValueError:
            continue
    raise llm_error(
        "could not parse JSON from the model response",
        outcome="invalid_response", retryable=False,
        hint="Use a model with reliable structured-output support or reduce the schema.",
        details={"snippet": text[:400], "attempted_fragment": fragment[:200]},
    )


def _close_unbalanced(text: str) -> str:
    """Append the closers needed to balance a truncated JSON fragment."""
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]" and stack:
            stack.pop()
    repaired = text.rstrip().rstrip(",")
    if in_string:
        repaired += '"'
    return repaired + "".join(reversed(stack))


async def complete_json(
    prompt: str,
    *,
    system: str = "",
    model: str = "",
    role: str = "chat",
    temperature: float = 0.1,
    max_tokens: int = 2048,
    purpose: str = "",
    run_id: str = "",
    retries: int = 1,
) -> Any:
    """Completion parsed as JSON, with one repair retry.

    Temperature defaults low because structured output does not benefit from
    sampling diversity. On a parse failure the model is asked again with the
    error attached, which recovers most cases; a truncation is reported as such
    rather than as malformed JSON.
    """
    last_error: Exception | None = None
    attempt_prompt = prompt
    for attempt in range(retries + 1):
        completion = await complete(
            attempt_prompt,
            system=system or "Respond with a single valid JSON value and nothing else.",
            model=model,
            role=role,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
            purpose=purpose or "json",
            run_id=run_id,
        )
        if completion.truncated:
            runs_store.mark_usage_failed(
                str(completion.raw.get("_usage_record_id") or "")
            )
            raise llm_error(
                f"the model hit its output limit ({max_tokens} tokens) before "
                f"finishing the JSON. Raise max_tokens or ask for less data.",
                outcome="output_truncated", provider=completion.provider,
                model=completion.model, retryable=True,
                hint="Raise max_tokens or request a smaller structured result.",
                details={"finish_reason": completion.finish_reason,
                         "max_tokens": max_tokens},
            )
        try:
            return extract_json(completion.text)
        except LLMError as exc:
            enrich_llm_error(
                exc, provider=completion.provider, model=completion.model
            )
            runs_store.mark_usage_failed(
                str(completion.raw.get("_usage_record_id") or "")
            )
            last_error = exc
            if attempt >= retries:
                break
            log.warning("JSON parse failed (attempt %s), retrying", attempt + 1)
            attempt_prompt = (
                f"{prompt}\n\nYour previous reply could not be parsed as JSON "
                f"({exc.message}). Reply with valid JSON only - no prose, no code "
                f"fence."
            )
    raise last_error or llm_error(
        "JSON completion failed", outcome="invalid_response", retryable=False
    )


async def embed_texts(
    texts: list[str], *, model: str = "", purpose: str = "embedding"
) -> list[list[float]]:
    """Embed texts through the configured embedding provider."""
    if not texts:
        return []
    backend, resolved_model = registry.resolve(model, role="embedding")
    started = time.perf_counter()
    try:
        vectors = await backend.embed(texts, model=resolved_model)
    except Exception as exc:
        runs_store.record_usage(
            provider=backend.provider_id, model=resolved_model, purpose=purpose,
            duration_ms=int((time.perf_counter() - started) * 1000), ok=False,
        )
        if isinstance(exc, LLMError):
            raise enrich_llm_error(
                exc, provider=backend.provider_id, model=resolved_model
            )
        raise llm_error(
            f"embedding provider '{backend.provider_id}' failed unexpectedly: {exc}",
            outcome="unexpected_error", provider=backend.provider_id,
            model=resolved_model, retryable=False,
            details={"cause_type": type(exc).__name__},
        ) from exc
    from ..core.util import estimate_tokens

    tokens = sum(estimate_tokens(t) for t in texts)
    runs_store.record_usage(
        provider=backend.provider_id, model=resolved_model, purpose=purpose,
        tokens_in=tokens, tokens_out=0,
        cost_usd=round(tokens / 1_000_000 * backend.config.price_in_per_mtok, 6),
        duration_ms=int((time.perf_counter() - started) * 1000), ok=True,
    )
    return vectors


async def test_provider(provider_id: str, model: str = "") -> dict[str, Any]:
    """Round-trip check used by the settings screen's "test" button."""
    started = time.perf_counter()
    try:
        backend = registry.build_backend(provider_id)
        resolved = model or backend.resolve_model()
        completion = await backend.complete(
            [Message(role="user", content="Reply with exactly: ok")],
            model=resolved, temperature=0.0, max_tokens=16,
        )
        return {
            "ok": True,
            "provider": provider_id,
            "model": completion.model,
            "reply": completion.text.strip()[:80],
            "tokens_in": completion.usage.prompt_tokens,
            "tokens_out": completion.usage.completion_tokens,
            "usage_reported": completion.usage.reported,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 - the failure is the answer here
        diagnostic = error_diagnostics(exc, provider=provider_id, model=model)
        return {
            "ok": False,
            "provider": provider_id,
            "error": str(exc),
            "error_type": type(exc).__name__,
            **diagnostic,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

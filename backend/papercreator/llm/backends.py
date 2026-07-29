"""Wire-protocol implementations for the four supported LLM shapes.

* :class:`OpenAIBackend` - ``/chat/completions``. Also covers DeepSeek,
  OpenRouter, Groq, Together, vLLM, LM Studio and any other OpenAI-compatible
  gateway, which is why it is the default ``kind``.
* :class:`AnthropicBackend` - ``/v1/messages``. Separate top-level ``system``
  parameter and a different SSE event vocabulary.
* :class:`GeminiBackend` - ``:generateContent``. Different field names throughout
  (``contents``/``parts``, ``generationConfig``).
* :class:`OllamaBackend` - local ``/api/chat``. Newline-delimited JSON rather than
  SSE, and no cost.

Shared behaviour lives in :func:`_request`: timeouts, bounded retries with
jitter, and error messages that name the provider and the actionable cause. Each
adapter only translates shapes.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..core.errors import LLMError, llm_error
from ..core.logging_setup import get_logger
from .base import Completion, LLMBackend, Message, StreamChunk, Usage

log = get_logger(__name__)

_CONNECT_TIMEOUT = 20.0


def _client(config: Any, *, stream: bool = False) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            config.timeout_s,
            connect=_CONNECT_TIMEOUT,
            # Use the configured deadline for both the first and later tokens.
            # Silently widening it to ten minutes made a broken local endpoint
            # look like a permanently running agent.
            read=config.timeout_s,
        ),
        follow_redirects=True,
    )


async def _request(
    config: Any,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST/GET with retries and a stable, machine-readable failure contract."""
    attempts = max(0, int(config.max_retries)) + 1
    last: str = ""
    for attempt in range(attempts):
        try:
            async with _client(config) as client:
                response = await client.request(
                    method, url, headers=headers, json=payload, params=params
                )
        except httpx.TimeoutException as exc:
            last = "timeout"
            if attempt + 1 >= attempts:
                raise llm_error(
                    f"provider '{config.id}' timed out after {config.timeout_s}s",
                    outcome="timeout", provider=config.id, retryable=True,
                    hint="Retry the call; if it repeats, raise the provider timeout.",
                    details={"url": url, "cause": str(exc)},
                ) from exc
        except httpx.HTTPError as exc:
            last = f"cannot reach {url}: {exc}"
            if attempt + 1 >= attempts:
                raise llm_error(
                    f"cannot connect to provider '{config.id}' at {config.base_url}. "
                    f"Check the base URL and that the service is running.",
                    outcome="network_error", provider=config.id, retryable=True,
                    hint="Check the network or local model service, then retry.",
                    details={"url": url, "cause": str(exc)},
                ) from exc
        else:
            if response.status_code == 401 or response.status_code == 403:
                raise llm_error(
                    f"provider '{config.id}' rejected the API key "
                    f"(HTTP {response.status_code}). Update it in Settings > Models.",
                    outcome="authentication_error", provider=config.id,
                    retryable=False, http_status=response.status_code,
                    hint="Update the API key in Settings > Models.",
                    details={"url": url, "body": response.text[:300]},
                )
            if response.status_code == 404:
                raise llm_error(
                    f"provider '{config.id}' returned 404 for {url}. The model name "
                    f"or base URL is probably wrong.",
                    outcome="configuration_error", provider=config.id,
                    retryable=False, http_status=404,
                    hint="Check the provider base URL and selected model name.",
                    details={"url": url, "body": response.text[:300]},
                )
            if response.status_code == 429:
                last = "rate limited"
                if attempt + 1 >= attempts:
                    retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                    raise llm_error(
                        f"provider '{config.id}' rate limit exceeded",
                        outcome="rate_limited", provider=config.id, retryable=True,
                        http_status=429, retry_after_s=retry_after,
                        hint="Wait for the provider limit to reset, then retry this run.",
                        details={"url": url, "body": response.text[:300]},
                    )
            elif response.status_code >= 400:
                # 4xx other than the above are deterministic - do not retry.
                if response.status_code < 500:
                    raise llm_error(
                        f"provider '{config.id}' returned HTTP "
                        f"{response.status_code}: {response.text[:300]}",
                        outcome="http_error", provider=config.id, retryable=False,
                        http_status=response.status_code,
                        hint="Check the request, model capabilities and provider settings.",
                        details={"url": url, "body": response.text[:300]},
                    )
                last = f"HTTP {response.status_code}"
                if attempt + 1 >= attempts:
                    raise llm_error(
                        f"provider '{config.id}' returned HTTP "
                        f"{response.status_code}",
                        outcome="http_error", provider=config.id, retryable=True,
                        http_status=response.status_code,
                        hint="The provider is temporarily unavailable; retry the run.",
                        details={"url": url, "body": response.text[:300]},
                    )
            else:
                try:
                    return response.json()
                except ValueError as exc:
                    raise llm_error(
                        f"provider '{config.id}' returned a non-JSON response",
                        outcome="invalid_response", provider=config.id,
                        retryable=False,
                        hint="Check that the base URL points to a compatible LLM API.",
                        details={"url": url, "snippet": response.text[:300]},
                    ) from exc
        delay = min(8.0, 0.6 * (2 ** attempt)) * (0.5 + random.random() * 0.5)
        await asyncio.sleep(delay)
    raise llm_error(
        f"provider '{config.id}' failed: {last}", outcome="model_error",
        provider=config.id, retryable=False,
    )


def _parse_retry_after(value: str | None) -> float | None:
    """Parse the common numeric Retry-After form without guessing dates."""
    try:
        return max(0.0, float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _stream_http_error(config: Any, response: httpx.Response, url: str) -> LLMError:
    status = response.status_code
    body = response.text[:300]
    if status in (401, 403):
        return llm_error(
            f"provider '{config.id}' rejected the streaming API key (HTTP {status})",
            outcome="authentication_error", provider=config.id, retryable=False,
            http_status=status, hint="Update the API key in Settings > Models.",
            details={"url": url, "body": body},
        )
    if status == 429:
        return llm_error(
            f"provider '{config.id}' streaming rate limit exceeded",
            outcome="rate_limited", provider=config.id, retryable=True,
            http_status=429,
            retry_after_s=_parse_retry_after(response.headers.get("Retry-After")),
            hint="Wait for the provider limit to reset, then retry this run.",
            details={"url": url, "body": body},
        )
    return llm_error(
        f"provider '{config.id}' streaming failed with HTTP {status}: {body}",
        outcome="http_error", provider=config.id, retryable=status >= 500,
        http_status=status,
        hint=("Retry the run; the provider appears temporarily unavailable."
              if status >= 500 else
              "Check the model capabilities, base URL and request settings."),
        details={"url": url, "body": body},
    )


async def _stream_lines(
    config: Any, url: str, headers: dict[str, str], payload: dict[str, Any]
) -> AsyncIterator[str]:
    """Yield raw lines; retry only before the first delta to avoid duplication."""
    attempts = max(0, int(config.max_retries)) + 1
    yielded_any = False
    for attempt in range(attempts):
        try:
            async with _client(config, stream=True) as client:
                async with client.stream(
                    "POST", url, headers=headers, json=payload
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        failure = _stream_http_error(config, response, url)
                        if failure.details.get("retryable") and attempt + 1 < attempts:
                            retry_after = float(failure.details.get("retry_after_s") or 0)
                            await asyncio.sleep(retry_after)
                            raise _RetryStream()
                        raise failure
                    async for line in response.aiter_lines():
                        if line:
                            yielded_any = True
                            yield line
                    return
        except _RetryStream:
            pass
        except httpx.TimeoutException as exc:
            if not yielded_any and attempt + 1 < attempts:
                pass
            else:
                outcome = "stream_interrupted" if yielded_any else "timeout"
                raise llm_error(
                    f"provider '{config.id}' stream timed out after "
                    f"{config.timeout_s}s",
                    outcome=outcome, provider=config.id, retryable=True,
                    hint="Retry the run; completed steps and the pre-run snapshot are kept.",
                    details={"url": url, "cause": str(exc)},
                ) from exc
        except httpx.HTTPError as exc:
            if not yielded_any and attempt + 1 < attempts:
                pass
            else:
                outcome = "stream_interrupted" if yielded_any else "network_error"
                raise llm_error(
                    f"provider '{config.id}' stream connection failed: {exc}",
                    outcome=outcome, provider=config.id, retryable=True,
                    hint="Check the provider connection and retry the run.",
                    details={"url": url, "cause": str(exc)},
                ) from exc
        if attempt + 1 < attempts:
            delay = min(8.0, 0.6 * (2 ** attempt)) * (0.5 + random.random() * 0.5)
            await asyncio.sleep(delay)


class _RetryStream(Exception):
    """Internal signal used to leave a response context before retrying."""


# --------------------------------------------------------------------- OpenAI


class OpenAIBackend(LLMBackend):
    """OpenAI ``/chat/completions``, and every gateway that mimics it."""

    kind = "openai"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _url(self, path: str) -> str:
        base = (self.config.base_url or "https://api.openai.com/v1").rstrip("/")
        return f"{base}{path}"

    def _payload(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        max_tokens: int,
        stop: list[str] | None,
        json_mode: bool,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if stop:
            payload["stop"] = stop[:4]
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if stream:
            # Ask for usage in the final SSE chunk; ignored by gateways that do
            # not support it, in which case usage is estimated.
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str = "",
        temperature: float = 0.4,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
        json_mode: bool = False,
    ) -> Completion:
        resolved = self.resolve_model(model)
        started = time.perf_counter()
        body = await _request(
            self.config, "POST", self._url("/chat/completions"),
            headers=self._headers(),
            payload=self._payload(
                messages, resolved, temperature, max_tokens, stop, json_mode, False
            ),
        )
        choices = body.get("choices") or []
        if not choices:
            raise llm_error(
                f"provider '{self.config.id}' returned no choices",
                outcome="invalid_response", provider=self.config.id,
                retryable=False,
                hint="Check that the endpoint implements OpenAI chat completions.",
                details={"body": str(body)[:300]},
            )
        message = choices[0].get("message") or {}
        text = message.get("content") or ""
        if not str(text).strip():
            raise llm_error(
                f"provider '{self.config.id}' returned an empty completion",
                outcome="empty_response", provider=self.config.id,
                retryable=True,
                hint="Retry once; if it repeats, check the selected model and content policy.",
                details={"body": str(body)[:300]},
            )
        raw_usage = body.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(raw_usage.get("prompt_tokens") or 0),
            completion_tokens=int(raw_usage.get("completion_tokens") or 0),
            reported=bool(raw_usage),
        )
        if not raw_usage:
            usage = self._estimate_usage(messages, text)
        return Completion(
            text=text,
            model=body.get("model") or resolved,
            provider=self.config.id,
            usage=usage,
            finish_reason=str(choices[0].get("finish_reason") or ""),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str = "",
        temperature: float = 0.4,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        resolved = self.resolve_model(model)
        started = time.perf_counter()
        collected: list[str] = []
        finish_reason = ""
        usage = Usage()
        saw_done = False
        async for line in _stream_lines(
            self.config, self._url("/chat/completions"), self._headers(),
            self._payload(
                messages, resolved, temperature, max_tokens, stop, False, True
            ),
        ):
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                saw_done = True
                break
            try:
                event = json.loads(data)
            except ValueError as exc:
                raise llm_error(
                    f"provider '{self.config.id}' sent invalid OpenAI SSE JSON",
                    outcome="invalid_response", provider=self.config.id,
                    retryable=False,
                    hint="Check that the endpoint is OpenAI streaming compatible.",
                    details={"snippet": data[:300]},
                ) from exc
            for choice in event.get("choices") or []:
                delta = (choice.get("delta") or {}).get("content") or ""
                if delta:
                    collected.append(delta)
                    yield StreamChunk(delta=delta)
                if choice.get("finish_reason"):
                    finish_reason = str(choice["finish_reason"])
            raw_usage = event.get("usage") or {}
            if raw_usage:
                usage = Usage(
                    prompt_tokens=int(raw_usage.get("prompt_tokens") or 0),
                    completion_tokens=int(raw_usage.get("completion_tokens") or 0),
                    reported=True,
                )
        text = "".join(collected)
        if not saw_done:
            raise llm_error(
                f"provider '{self.config.id}' closed the OpenAI stream before [DONE]",
                outcome="stream_interrupted", provider=self.config.id,
                retryable=True,
                hint="Retry the run; partial text is preserved in the failed step audit.",
                details={"partial_output": text,
                         "partial_output_chars": len(text)},
            )
        if not text.strip():
            raise llm_error(
                f"provider '{self.config.id}' returned an empty stream",
                outcome="empty_response", provider=self.config.id,
                retryable=True,
                hint="Retry once; if it repeats, check the selected model.",
            )
        if not usage.reported:
            usage = self._estimate_usage(messages, text)
        yield StreamChunk(done=True, completion=Completion(
            text=text, model=resolved, provider=self.config.id, usage=usage,
            finish_reason=finish_reason,
            duration_ms=int((time.perf_counter() - started) * 1000),
        ))

    async def embed(self, texts: list[str], *, model: str = "") -> list[list[float]]:
        resolved = model or "text-embedding-3-small"
        vectors: list[list[float]] = []
        # Batch to stay under request size limits on long abstracts.
        for start in range(0, len(texts), 96):
            batch = texts[start: start + 96]
            body = await _request(
                self.config, "POST", self._url("/embeddings"),
                headers=self._headers(),
                payload={"model": resolved, "input": batch},
            )
            for item in sorted(
                body.get("data") or [], key=lambda d: int(d.get("index") or 0)
            ):
                vectors.append([float(v) for v in item.get("embedding") or []])
        if len(vectors) != len(texts):
            raise llm_error(
                f"embedding provider returned {len(vectors)} vectors for "
                f"{len(texts)} inputs",
                outcome="invalid_response", provider=self.config.id,
                model=resolved, retryable=False,
                hint="Check that the endpoint implements OpenAI embeddings.",
            )
        return vectors

    async def list_models(self) -> list[str]:
        try:
            body = await _request(
                self.config, "GET", self._url("/models"), headers=self._headers()
            )
        except LLMError as exc:
            log.debug("could not list models for %s: %s", self.config.id, exc)
            return list(self.config.models)
        names = [
            str(item.get("id"))
            for item in (body.get("data") or []) if item.get("id")
        ]
        return sorted(names) or list(self.config.models)


# ------------------------------------------------------------------ Anthropic


class AnthropicBackend(LLMBackend):
    """Anthropic ``/v1/messages``.

    Differences from the OpenAI shape that this adapter absorbs: the system
    prompt is a top-level parameter rather than a message; ``max_tokens`` is
    required; and streaming uses named SSE events
    (``content_block_delta``/``message_delta``) instead of choice deltas.
    """

    kind = "anthropic"
    api_version = "2023-06-01"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": self.api_version,
        }

    def _url(self, path: str) -> str:
        base = (self.config.base_url or "https://api.anthropic.com").rstrip("/")
        return f"{base}{path}"

    def _payload(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        max_tokens: int,
        stop: list[str] | None,
        stream: bool,
    ) -> dict[str, Any]:
        system_parts = [m.content for m in messages if m.role == "system"]
        conversation = [m.to_dict() for m in messages if m.role != "system"]
        if not conversation:
            # The API requires at least one non-system message.
            conversation = [{"role": "user", "content": "\n".join(system_parts)
                             or "Continue."}]
            system_parts = []
        payload: dict[str, Any] = {
            "model": model,
            "messages": conversation,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": stream,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if stop:
            payload["stop_sequences"] = stop[:4]
        return payload

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str = "",
        temperature: float = 0.4,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
        json_mode: bool = False,
    ) -> Completion:
        resolved = self.resolve_model(model)
        working = list(messages)
        if json_mode:
            # No response_format parameter; the reliable technique is an explicit
            # instruction plus a prefilled assistant turn opening the object.
            working.append(Message(role="assistant", content="{"))
        started = time.perf_counter()
        body = await _request(
            self.config, "POST", self._url("/v1/messages"),
            headers=self._headers(),
            payload=self._payload(
                working, resolved, temperature, max_tokens, stop, False
            ),
        )
        blocks = body.get("content") or []
        text = "".join(
            block.get("text") or "" for block in blocks if block.get("type") == "text"
        )
        if json_mode and text and not text.lstrip().startswith("{"):
            # Re-attach the prefill so the caller receives valid JSON.
            text = "{" + text
        if not text.strip():
            raise llm_error(
                f"provider '{self.config.id}' returned an empty completion",
                outcome="empty_response", provider=self.config.id,
                retryable=True,
                hint="Retry once; if it repeats, check the selected model and content policy.",
                details={"body": str(body)[:300]},
            )
        raw_usage = body.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(raw_usage.get("input_tokens") or 0),
            completion_tokens=int(raw_usage.get("output_tokens") or 0),
            reported=bool(raw_usage),
        )
        if not raw_usage:
            usage = self._estimate_usage(messages, text)
        return Completion(
            text=text,
            model=body.get("model") or resolved,
            provider=self.config.id,
            usage=usage,
            finish_reason=str(body.get("stop_reason") or ""),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str = "",
        temperature: float = 0.4,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        resolved = self.resolve_model(model)
        started = time.perf_counter()
        collected: list[str] = []
        finish_reason = ""
        usage = Usage()
        saw_stop = False
        async for line in _stream_lines(
            self.config, self._url("/v1/messages"), self._headers(),
            self._payload(messages, resolved, temperature, max_tokens, stop, True),
        ):
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except ValueError as exc:
                raise llm_error(
                    f"provider '{self.config.id}' sent invalid Anthropic SSE JSON",
                    outcome="invalid_response", provider=self.config.id,
                    retryable=False,
                    hint="Check that the endpoint implements Anthropic message streaming.",
                    details={"snippet": line[5:].strip()[:300]},
                ) from exc
            event_type = event.get("type")
            if event_type == "content_block_delta":
                delta = (event.get("delta") or {}).get("text") or ""
                if delta:
                    collected.append(delta)
                    yield StreamChunk(delta=delta)
            elif event_type == "message_start":
                raw_usage = (event.get("message") or {}).get("usage") or {}
                if raw_usage:
                    usage.prompt_tokens = int(raw_usage.get("input_tokens") or 0)
                    usage.reported = True
            elif event_type == "message_delta":
                finish_reason = str(
                    (event.get("delta") or {}).get("stop_reason") or finish_reason
                )
                raw_usage = event.get("usage") or {}
                if raw_usage:
                    usage.completion_tokens = int(raw_usage.get("output_tokens") or 0)
                    usage.reported = True
            elif event_type == "message_stop":
                saw_stop = True
        text = "".join(collected)
        if not saw_stop:
            raise llm_error(
                f"provider '{self.config.id}' closed the Anthropic stream before message_stop",
                outcome="stream_interrupted", provider=self.config.id,
                retryable=True,
                hint="Retry the run; partial text is preserved in the failed step audit.",
                details={"partial_output": text,
                         "partial_output_chars": len(text)},
            )
        if not text.strip():
            raise llm_error(
                f"provider '{self.config.id}' returned an empty stream",
                outcome="empty_response", provider=self.config.id,
                retryable=True,
            )
        if not usage.reported:
            usage = self._estimate_usage(messages, text)
        yield StreamChunk(done=True, completion=Completion(
            text=text, model=resolved, provider=self.config.id, usage=usage,
            finish_reason=finish_reason,
            duration_ms=int((time.perf_counter() - started) * 1000),
        ))


# --------------------------------------------------------------------- Gemini


class GeminiBackend(LLMBackend):
    """Google Generative Language ``:generateContent``.

    Field names differ throughout: ``contents`` with ``parts``, ``role: model``
    instead of ``assistant``, ``systemInstruction`` separate, and configuration
    nested under ``generationConfig``.
    """

    kind = "gemini"

    def _url(self, model: str, *, stream: bool) -> str:
        base = (
            self.config.base_url or "https://generativelanguage.googleapis.com"
        ).rstrip("/")
        action = "streamGenerateContent" if stream else "generateContent"
        return f"{base}/v1beta/models/{model}:{action}"

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json",
                "x-goog-api-key": self.config.api_key}

    def _payload(
        self,
        messages: list[Message],
        temperature: float,
        max_tokens: int,
        stop: list[str] | None,
        json_mode: bool,
    ) -> dict[str, Any]:
        contents = [
            {
                "role": "model" if m.role == "assistant" else "user",
                "parts": [{"text": m.content}],
            }
            for m in messages if m.role != "system"
        ]
        system_parts = [m.content for m in messages if m.role == "system"]
        if not contents:
            contents = [{"role": "user", "parts": [{"text": "\n".join(system_parts)
                                                    or "Continue."}]}]
        config: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if stop:
            config["stopSequences"] = stop[:5]
        if json_mode:
            config["responseMimeType"] = "application/json"
        payload: dict[str, Any] = {"contents": contents, "generationConfig": config}
        if system_parts:
            payload["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_parts)}]
            }
        return payload

    @staticmethod
    def _extract(body: dict[str, Any]) -> tuple[str, str]:
        candidates = body.get("candidates") or []
        if not candidates:
            return "", ""
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(part.get("text") or "" for part in parts)
        return text, str(candidates[0].get("finishReason") or "")

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str = "",
        temperature: float = 0.4,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
        json_mode: bool = False,
    ) -> Completion:
        resolved = self.resolve_model(model)
        started = time.perf_counter()
        body = await _request(
            self.config, "POST", self._url(resolved, stream=False),
            headers=self._headers(),
            payload=self._payload(messages, temperature, max_tokens, stop, json_mode),
        )
        text, finish_reason = self._extract(body)
        if not text.strip():
            raise llm_error(
                f"provider '{self.config.id}' returned an empty completion",
                outcome="empty_response", provider=self.config.id,
                retryable=True,
                hint="Retry once; if it repeats, check the selected Gemini model.",
                details={"body": str(body)[:300]},
            )
        raw_usage = body.get("usageMetadata") or {}
        usage = Usage(
            prompt_tokens=int(raw_usage.get("promptTokenCount") or 0),
            completion_tokens=int(raw_usage.get("candidatesTokenCount") or 0),
            reported=bool(raw_usage),
        )
        if not raw_usage:
            usage = self._estimate_usage(messages, text)
        return Completion(
            text=text, model=resolved, provider=self.config.id, usage=usage,
            finish_reason=finish_reason,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str = "",
        temperature: float = 0.4,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        resolved = self.resolve_model(model)
        started = time.perf_counter()
        collected: list[str] = []
        finish_reason = ""
        usage = Usage()
        url = f"{self._url(resolved, stream=True)}?alt=sse"
        async for line in _stream_lines(
            self.config, url, self._headers(),
            self._payload(messages, temperature, max_tokens, stop, False),
        ):
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except ValueError as exc:
                raise llm_error(
                    f"provider '{self.config.id}' sent invalid Gemini SSE JSON",
                    outcome="invalid_response", provider=self.config.id,
                    retryable=False,
                    hint="Check that the endpoint implements Gemini SSE streaming.",
                    details={"snippet": line[5:].strip()[:300]},
                ) from exc
            text, reason = self._extract(event)
            if text:
                collected.append(text)
                yield StreamChunk(delta=text)
            if reason:
                finish_reason = reason
            raw_usage = event.get("usageMetadata") or {}
            if raw_usage:
                usage = Usage(
                    prompt_tokens=int(raw_usage.get("promptTokenCount") or 0),
                    completion_tokens=int(raw_usage.get("candidatesTokenCount") or 0),
                    reported=True,
                )
        text = "".join(collected)
        if not finish_reason:
            raise llm_error(
                f"provider '{self.config.id}' closed the Gemini stream without a finish reason",
                outcome="stream_interrupted", provider=self.config.id,
                retryable=True,
                hint="Retry the run; partial text is preserved in the failed step audit.",
                details={"partial_output": text,
                         "partial_output_chars": len(text)},
            )
        if not text.strip():
            raise llm_error(
                f"provider '{self.config.id}' returned an empty stream",
                outcome="empty_response", provider=self.config.id,
                retryable=True,
            )
        if not usage.reported:
            usage = self._estimate_usage(messages, text)
        yield StreamChunk(done=True, completion=Completion(
            text=text, model=resolved, provider=self.config.id, usage=usage,
            finish_reason=finish_reason,
            duration_ms=int((time.perf_counter() - started) * 1000),
        ))

    async def embed(self, texts: list[str], *, model: str = "") -> list[list[float]]:
        resolved = model or "text-embedding-004"
        base = (
            self.config.base_url or "https://generativelanguage.googleapis.com"
        ).rstrip("/")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 100):
            batch = texts[start: start + 100]
            body = await _request(
                self.config, "POST",
                f"{base}/v1beta/models/{resolved}:batchEmbedContents",
                headers=self._headers(),
                payload={"requests": [
                    {"model": f"models/{resolved}",
                     "content": {"parts": [{"text": text}]}}
                    for text in batch
                ]},
            )
            for item in body.get("embeddings") or []:
                vectors.append([float(v) for v in item.get("values") or []])
        if len(vectors) != len(texts) or any(not vector for vector in vectors):
            raise llm_error(
                f"embedding provider returned {len(vectors)} valid vectors for "
                f"{len(texts)} inputs",
                outcome="invalid_response", provider=self.config.id,
                model=resolved, retryable=False,
                hint="Check the Gemini embedding model and endpoint compatibility.",
            )
        return vectors

    async def list_models(self) -> list[str]:
        base = (
            self.config.base_url or "https://generativelanguage.googleapis.com"
        ).rstrip("/")
        try:
            body = await _request(
                self.config, "GET", f"{base}/v1beta/models",
                headers=self._headers(),
            )
        except LLMError:
            return list(self.config.models)
        return sorted(
            str(m.get("name", "")).replace("models/", "")
            for m in (body.get("models") or [])
            if "generateContent" in (m.get("supportedGenerationMethods") or [])
        ) or list(self.config.models)


# --------------------------------------------------------------------- Ollama


class OllamaBackend(LLMBackend):
    """Local Ollama ``/api/chat``.

    Streams newline-delimited JSON objects rather than SSE. No API key and no
    cost, which makes it the recommended zero-cost path for users who would
    rather not send manuscripts to a hosted API.
    """

    kind = "ollama"

    def _url(self, path: str) -> str:
        base = (self.config.base_url or "http://127.0.0.1:11434").rstrip("/")
        return f"{base}{path}"

    def _payload(
        self,
        messages: list[Message],
        model: str,
        temperature: float,
        max_tokens: int,
        stop: list[str] | None,
        stream: bool,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {
            "temperature": temperature,
            "num_predict": max_tokens,
        }
        if stop:
            options["stop"] = stop[:4]
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_dict() for m in messages],
            "stream": stream,
            "options": options,
        }
        if json_mode:
            payload["format"] = "json"
        return payload

    async def complete(
        self,
        messages: list[Message],
        *,
        model: str = "",
        temperature: float = 0.4,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
        json_mode: bool = False,
    ) -> Completion:
        resolved = self.resolve_model(model)
        started = time.perf_counter()
        body = await _request(
            self.config, "POST", self._url("/api/chat"),
            headers={"Content-Type": "application/json"},
            payload=self._payload(
                messages, resolved, temperature, max_tokens, stop, False, json_mode
            ),
        )
        text = ((body.get("message") or {}).get("content")) or ""
        if not text.strip():
            raise llm_error(
                f"provider '{self.config.id}' returned an empty completion",
                outcome="empty_response", provider=self.config.id,
                retryable=True,
                hint="Retry once; if it repeats, confirm the Ollama model is installed.",
                details={"body": str(body)[:300]},
            )
        usage = Usage(
            prompt_tokens=int(body.get("prompt_eval_count") or 0),
            completion_tokens=int(body.get("eval_count") or 0),
            reported=bool(body.get("eval_count")),
        )
        if not usage.reported:
            usage = self._estimate_usage(messages, text)
        return Completion(
            text=text, model=resolved, provider=self.config.id, usage=usage,
            finish_reason="stop" if body.get("done") else "",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str = "",
        temperature: float = 0.4,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        resolved = self.resolve_model(model)
        started = time.perf_counter()
        collected: list[str] = []
        usage = Usage()
        saw_done = False
        async for line in _stream_lines(
            self.config, self._url("/api/chat"),
            {"Content-Type": "application/json"},
            self._payload(messages, resolved, temperature, max_tokens, stop, True),
        ):
            try:
                event = json.loads(line)
            except ValueError as exc:
                raise llm_error(
                    f"provider '{self.config.id}' sent invalid Ollama stream JSON",
                    outcome="invalid_response", provider=self.config.id,
                    retryable=False,
                    hint="Check the Ollama version and configured base URL.",
                    details={"snippet": line[:300]},
                ) from exc
            delta = ((event.get("message") or {}).get("content")) or ""
            if delta:
                collected.append(delta)
                yield StreamChunk(delta=delta)
            if event.get("done"):
                saw_done = True
                usage = Usage(
                    prompt_tokens=int(event.get("prompt_eval_count") or 0),
                    completion_tokens=int(event.get("eval_count") or 0),
                    reported=bool(event.get("eval_count")),
                )
        text = "".join(collected)
        if not saw_done:
            raise llm_error(
                f"provider '{self.config.id}' closed the Ollama stream before done=true",
                outcome="stream_interrupted", provider=self.config.id,
                retryable=True,
                hint="Confirm Ollama is running, then retry the run.",
                details={"partial_output": text,
                         "partial_output_chars": len(text)},
            )
        if not text.strip():
            raise llm_error(
                f"provider '{self.config.id}' returned an empty stream",
                outcome="empty_response", provider=self.config.id,
                retryable=True,
            )
        if not usage.reported:
            usage = self._estimate_usage(messages, text)
        yield StreamChunk(done=True, completion=Completion(
            text=text, model=resolved, provider=self.config.id, usage=usage,
            finish_reason="stop",
            duration_ms=int((time.perf_counter() - started) * 1000),
        ))

    async def embed(self, texts: list[str], *, model: str = "") -> list[list[float]]:
        resolved = model or self.config.default_model or "nomic-embed-text"
        vectors: list[list[float]] = []
        for text in texts:
            body = await _request(
                self.config, "POST", self._url("/api/embeddings"),
                headers={"Content-Type": "application/json"},
                payload={"model": resolved, "prompt": text},
            )
            vector = [float(v) for v in body.get("embedding") or []]
            if not vector:
                raise llm_error(
                    f"provider '{self.config.id}' returned an empty embedding",
                    outcome="invalid_response", provider=self.config.id,
                    model=resolved, retryable=False,
                    hint="Confirm that the Ollama embedding model is installed.",
                )
            vectors.append(vector)
        return vectors

    async def list_models(self) -> list[str]:
        try:
            body = await _request(
                self.config, "GET", self._url("/api/tags"),
                headers={"Content-Type": "application/json"},
            )
        except LLMError:
            return list(self.config.models)
        return sorted(
            str(m.get("name")) for m in (body.get("models") or []) if m.get("name")
        ) or list(self.config.models)


BACKENDS: dict[str, type[LLMBackend]] = {
    "openai": OpenAIBackend,
    "anthropic": AnthropicBackend,
    "gemini": GeminiBackend,
    "ollama": OllamaBackend,
}

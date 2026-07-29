"""LLM provider contract.

One abstraction over four wire protocols. ``openai`` is deliberately the widest
net: DeepSeek, OpenRouter, Groq, Together, vLLM, LM Studio, llama.cpp's server
and most self-hosted gateways all speak the OpenAI chat-completions shape, so a
single implementation reaches nearly every model the user might want. Anthropic,
Gemini and Ollama have genuinely different request/response formats and get their
own adapters.

The contract every backend satisfies:

* :meth:`LLMBackend.complete` - one non-streaming call, returns
  :class:`Completion` with usage.
* :meth:`LLMBackend.stream` - async iterator of text deltas, ending with a final
  :class:`Completion`. Streaming matters because a section draft takes 30+
  seconds and the user needs to see it arrive.
* :meth:`LLMBackend.embed` - optional; raises
  :class:`~papercreator.core.errors.LLMError` when the backend has no embedding
  endpoint.

Usage accounting is mandatory, not optional: every call records tokens and
estimated cost so the workbench can show what a paper cost to write.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from ..core.config import LLMProviderConfig
from ..core.errors import LLMError, llm_error
from ..core.logging_setup import get_logger

log = get_logger(__name__)

Role = Literal["system", "user", "assistant"]


@dataclass
class Message:
    role: Role
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # True when the numbers came from the provider; False when estimated locally
    # (Ollama and some gateways omit usage). Recorded so cost reports can be
    # honest about their precision.
    reported: bool = False

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class Completion:
    text: str
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = ""
    duration_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def truncated(self) -> bool:
        """True when the model stopped because it hit the token ceiling.

        Callers that parse structured output check this: a JSON object cut off
        mid-string is a configuration problem (max_tokens too low), not a
        model failure, and the error message should say so.
        """
        return self.finish_reason in ("length", "max_tokens", "MAX_TOKENS")


@dataclass
class StreamChunk:
    """One streamed delta. ``done`` marks the terminal chunk."""

    delta: str = ""
    done: bool = False
    completion: Completion | None = None


class LLMBackend(ABC):
    """Base class for a wire-protocol implementation."""

    kind: str = ""

    def __init__(self, config: LLMProviderConfig) -> None:
        self.config = config

    @property
    def provider_id(self) -> str:
        return self.config.id

    def resolve_model(self, model: str = "") -> str:
        resolved = model or self.config.default_model
        if not resolved:
            raise llm_error(
                f"no model specified for provider '{self.config.id}' and it has no "
                f"default_model configured",
                outcome="configuration_error", provider=self.config.id,
                retryable=False,
                hint="Choose a default model in Settings > Models.",
            )
        return resolved

    @abstractmethod
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
        """One completion. Must populate ``usage`` (estimating if necessary)."""

    @abstractmethod
    def stream(
        self,
        messages: list[Message],
        *,
        model: str = "",
        temperature: float = 0.4,
        max_tokens: int = 4096,
        stop: list[str] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream deltas, then one final chunk with ``done=True``."""

    async def embed(self, texts: list[str], *, model: str = "") -> list[list[float]]:
        raise llm_error(
            f"provider '{self.config.id}' ({self.kind}) has no embedding endpoint",
            outcome="unavailable", provider=self.config.id, retryable=False,
            hint="Select a provider with an embedding endpoint.",
        )

    async def list_models(self) -> list[str]:
        """Models the endpoint reports. Empty list when it cannot be queried."""
        return list(self.config.models)

    def estimate_cost(self, usage: Usage) -> float:
        """Cost in USD from the configured per-million-token prices.

        Zero when the user has not entered prices - the UI then shows token
        counts only, rather than a made-up number.
        """
        return round(
            usage.prompt_tokens / 1_000_000 * self.config.price_in_per_mtok
            + usage.completion_tokens / 1_000_000 * self.config.price_out_per_mtok,
            6,
        )

    def _estimate_usage(self, messages: list[Message], output: str) -> Usage:
        """Local token estimate for backends that do not report usage."""
        from ..core.util import estimate_tokens

        prompt = sum(estimate_tokens(m.content) for m in messages)
        return Usage(
            prompt_tokens=prompt,
            completion_tokens=estimate_tokens(output),
            reported=False,
        )

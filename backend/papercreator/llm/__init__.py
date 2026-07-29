"""LLM layer: one interface over four wire protocols.

Public surface::

    from papercreator.llm import client, registry
    from papercreator.llm.base import Message

    await client.complete(prompt, system=..., purpose="draft")
    await client.complete_json(prompt)             # parsed + repaired JSON
    async for chunk in client.stream(prompt): ...  # token deltas
    await client.embed_texts(texts)
    registry.has_any_provider()                    # is anything configured?
    registry.status()                              # for /api/system/health

``kind="openai"`` covers OpenAI plus every compatible gateway (DeepSeek,
OpenRouter, Groq, Together, vLLM, LM Studio), so four adapters reach nearly any
model. Ollama gives a fully local, zero-cost path.

Every call is metered into the ``llm_usage`` table.
See ``docs/systems/llm_system.md``.
"""

from . import client, registry  # noqa: F401
from .base import Completion, LLMBackend, Message, StreamChunk, Usage  # noqa: F401

__all__ = [
    "Completion",
    "LLMBackend",
    "Message",
    "StreamChunk",
    "Usage",
    "client",
    "registry",
]

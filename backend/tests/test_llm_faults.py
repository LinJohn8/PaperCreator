"""Deterministic LLM transport, accounting and agent-recovery failure tests.

No test in this module contacts a real model provider.  MockTransport exercises
the exact HTTP/SSE adapters while the agent test uses a deliberately failing
role so the Run/Step/Job persistence contract can be asserted end to end.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from papercreator.core.config import LLMProviderConfig
from papercreator.core.errors import LLMError, llm_error
from papercreator.llm.base import Completion, Message, StreamChunk


def _config(*, retries: int = 0) -> LLMProviderConfig:
    return LLMProviderConfig(
        id="fault-provider",
        kind="openai",
        base_url="https://llm.test/v1",
        api_key="test-only",
        default_model="fault-model",
        timeout_s=0.2,
        max_retries=retries,
    )


def _install_transport(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    from papercreator.llm import backends

    def factory(_config, *, stream: bool = False):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(backends, "_client", factory)


class TestLlmHttpFailures:
    @pytest.mark.parametrize(
        ("status", "outcome", "retryable"),
        [
            (401, "authentication_error", False),
            (429, "rate_limited", True),
            (503, "http_error", True),
            (400, "http_error", False),
        ],
    )
    def test_http_statuses_have_stable_diagnostics(
        self, monkeypatch, status: int, outcome: str, retryable: bool
    ):
        from papercreator.llm import backends

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status,
                text="provider fault",
                headers={"Retry-After": "7"},
                request=request,
            )

        _install_transport(monkeypatch, handler)
        with pytest.raises(LLMError) as raised:
            asyncio.run(
                backends._request(
                    _config(), "POST", "https://llm.test/v1/chat/completions",
                    headers={}, payload={"model": "fault-model"},
                )
            )
        details = raised.value.details
        assert details["outcome"] == outcome
        assert details["error_code"] == f"llm_{outcome}"
        assert details["retryable"] is retryable
        assert details["http_status"] == status
        assert details["hint"]
        if status == 429:
            assert details["retry_after_s"] == 7.0

    @pytest.mark.parametrize(
        ("failure", "outcome"),
        [
            (httpx.ReadTimeout("too slow"), "timeout"),
            (httpx.ConnectError("offline"), "network_error"),
        ],
    )
    def test_transport_failures_are_classified(
        self, monkeypatch, failure: httpx.HTTPError, outcome: str
    ):
        from papercreator.llm import backends

        def handler(request: httpx.Request) -> httpx.Response:
            failure.request = request
            raise failure

        _install_transport(monkeypatch, handler)
        with pytest.raises(LLMError) as raised:
            asyncio.run(
                backends._request(
                    _config(), "POST", "https://llm.test/v1/chat/completions",
                    headers={}, payload={},
                )
            )
        assert raised.value.details["outcome"] == outcome
        assert raised.value.details["retryable"] is True

    def test_non_json_success_is_invalid_response(self, monkeypatch):
        from papercreator.llm import backends

        _install_transport(
            monkeypatch,
            lambda request: httpx.Response(200, text="<html>wrong endpoint</html>",
                                            request=request),
        )
        with pytest.raises(LLMError) as raised:
            asyncio.run(
                backends._request(
                    _config(), "POST", "https://llm.test/v1/chat/completions",
                    headers={}, payload={},
                )
            )
        assert raised.value.details["outcome"] == "invalid_response"


class TestOpenAiProtocolFailures:
    def _backend(self):
        from papercreator.llm.backends import OpenAIBackend

        return OpenAIBackend(_config())

    def test_missing_choices_and_empty_text_are_rejected(self, monkeypatch):
        bodies = iter(
            [
                {"id": "one", "choices": []},
                {"id": "two", "choices": [{"message": {"content": ""}}]},
            ]
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=next(bodies), request=request)

        _install_transport(monkeypatch, handler)
        backend = self._backend()
        for expected in ("invalid_response", "empty_response"):
            with pytest.raises(LLMError) as raised:
                asyncio.run(
                    backend.complete([Message(role="user", content="hello")],
                                     model="fault-model")
                )
            assert raised.value.details["outcome"] == expected

    def test_bad_sse_json_is_not_silently_ignored(self, monkeypatch):
        _install_transport(
            monkeypatch,
            lambda request: httpx.Response(
                200, content=b"data: {not-json}\n\ndata: [DONE]\n\n", request=request
            ),
        )

        async def consume() -> None:
            async for _ in self._backend().stream(
                [Message(role="user", content="hello")], model="fault-model"
            ):
                pass

        with pytest.raises(LLMError) as raised:
            asyncio.run(consume())
        assert raised.value.details["outcome"] == "invalid_response"

    def test_eof_without_done_preserves_partial_output(self, monkeypatch):
        event = b'data: {"choices":[{"delta":{"content":"half draft"}}]}\n\n'
        _install_transport(
            monkeypatch,
            lambda request: httpx.Response(200, content=event, request=request),
        )

        async def consume() -> list[str]:
            chunks: list[str] = []
            async for chunk in self._backend().stream(
                [Message(role="user", content="hello")], model="fault-model"
            ):
                chunks.append(chunk.delta)
            return chunks

        with pytest.raises(LLMError) as raised:
            asyncio.run(consume())
        assert raised.value.details["outcome"] == "stream_interrupted"
        assert raised.value.details["partial_output"] == "half draft"
        assert raised.value.details["partial_output_chars"] == 10

    @pytest.mark.parametrize(
        ("backend_name", "wire", "partial"),
        [
            (
                "anthropic",
                b'data: {"type":"content_block_delta","delta":{"text":"anthropic half"}}\n\n',
                "anthropic half",
            ),
            (
                "gemini",
                b'data: {"candidates":[{"content":{"parts":[{"text":"gemini half"}]}}]}\n\n',
                "gemini half",
            ),
            (
                "ollama",
                b'{"message":{"content":"ollama half"},"done":false}\n',
                "ollama half",
            ),
        ],
    )
    def test_every_protocol_rejects_a_missing_terminal_event(
        self, monkeypatch, backend_name: str, wire: bytes, partial: str
    ):
        from papercreator.llm import backends

        classes = {
            "anthropic": backends.AnthropicBackend,
            "gemini": backends.GeminiBackend,
            "ollama": backends.OllamaBackend,
        }
        config = _config().model_copy(update={"kind": backend_name})
        backend = classes[backend_name](config)
        _install_transport(
            monkeypatch,
            lambda request: httpx.Response(200, content=wire, request=request),
        )

        async def consume() -> None:
            async for _ in backend.stream(
                [Message(role="user", content="hello")], model="fault-model"
            ):
                pass

        with pytest.raises(LLMError) as raised:
            asyncio.run(consume())
        assert raised.value.details["outcome"] == "stream_interrupted"
        assert raised.value.details["partial_output"] == partial


class TestLlmAccounting:
    def test_json_parse_failure_reclassifies_the_same_usage_row(
        self, temp_home, monkeypatch
    ):
        from papercreator.llm import backends, client, registry
        from papercreator.store import runs as runs_store

        body = {
            "model": "fault-model",
            "choices": [{"message": {"content": "plain prose only"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        }
        _install_transport(
            monkeypatch,
            lambda request: httpx.Response(200, json=body, request=request),
        )
        backend = backends.OpenAIBackend(_config())
        monkeypatch.setattr(registry, "resolve", lambda *_args, **_kwargs:
                            (backend, "fault-model"))
        before = runs_store.usage_summary()["totals"]
        with pytest.raises(LLMError):
            asyncio.run(
                client.complete_json("return JSON", retries=0,
                                     purpose="fault-json-ledger")
            )
        after = runs_store.usage_summary()["totals"]
        assert after["calls"] == before["calls"] + 1
        assert after["failures"] == before["failures"] + 1

    def test_interrupted_stream_records_one_failed_call_with_partial_tokens(
        self, temp_home, monkeypatch
    ):
        from papercreator.core.db import query_one
        from papercreator.llm import backends, client, registry

        event = b'data: {"choices":[{"delta":{"content":"partial words"}}]}\n\n'
        _install_transport(
            monkeypatch,
            lambda request: httpx.Response(200, content=event, request=request),
        )
        backend = backends.OpenAIBackend(_config())
        monkeypatch.setattr(registry, "resolve", lambda *_args, **_kwargs:
                            (backend, "fault-model"))

        async def consume() -> None:
            async for _ in client.stream("draft", purpose="fault-stream-ledger"):
                pass

        with pytest.raises(LLMError) as raised:
            asyncio.run(consume())
        row = query_one(
            "SELECT ok, tokens_in, tokens_out FROM llm_usage WHERE purpose=? "
            "ORDER BY rowid DESC LIMIT 1", ("fault-stream-ledger",)
        )
        assert row is not None and row["ok"] == 0
        assert row["tokens_in"] > 0 and row["tokens_out"] > 0
        assert raised.value.details["partial_output"] == "partial words"


class TestAgentFailureRecovery:
    def test_failed_step_run_and_job_share_diagnostics_and_snapshots(
        self, project, monkeypatch
    ):
        from papercreator.agents import orchestrator
        from papercreator.agents.base import Agent, AgentResult
        from papercreator.core.jobs import manager
        from papercreator.store import runs as runs_store
        from papercreator.store import snapshots as snapshots_store

        class FailingAgent(Agent):
            name = "fault_writer"
            title = "Fault writer"

            async def run(self, board) -> AgentResult:
                raise llm_error(
                    "stream disconnected after a partial paragraph",
                    outcome="stream_interrupted", provider="fault-provider",
                    model="fault-model", retryable=True,
                    hint="Retry the run after checking the provider.",
                    details={
                        "partial_output": "A retained partial paragraph.",
                        "partial_output_chars": 29,
                        "partial_tokens_in": 8,
                        "partial_tokens_out": 5,
                    },
                )

        async def failing_custom(self, board):
            await self._step(self._make(FailingAgent), board)
            return {"mode": "custom"}

        monkeypatch.setattr(orchestrator.llm_registry, "has_any_provider", lambda: True)
        monkeypatch.setattr(orchestrator.Orchestrator, "_run_custom", failing_custom)
        submitted = orchestrator.submit_run(
            project_id=project.id, pipeline="custom", custom_roles=["planner"]
        )
        job = manager.wait(submitted["job_id"], timeout=10)
        run = runs_store.require_run(submitted["run_id"])

        assert job["status"] == "failed"
        assert job["result"]["failure"]["outcome"] == "stream_interrupted"
        assert run["status"] == "failed"
        assert run["result"]["failure"]["outcome"] == "stream_interrupted"
        assert run["result"]["recovery"]["strategy"] == "partial_work_preserved"
        assert run["result"]["recovery"]["retryable"] is True
        assert run["request"]["custom_roles"] == ["planner"]
        assert "paper_ids" in run["request"] and "skill_ids" in run["request"]

        step = run["steps"][0]
        assert step["status"] == "failed"
        assert step["output"] == "A retained partial paragraph."
        assert step["tokens_in"] == 8 and step["tokens_out"] == 5
        assert step["meta"]["failure"]["outcome"] == "stream_interrupted"
        assert step["meta"]["partial_output_kept"] is True

        snapshot_ids = {
            item["id"] for item in snapshots_store.list_snapshots(project.id)
        }
        assert run["result"]["snapshots"]["before"] in snapshot_ids
        assert run["result"]["snapshots"]["after"] in snapshot_ids
        # A fresh read is the restart-survival contract: diagnostics are not
        # held only in the worker or SSE event.
        reloaded = runs_store.require_run(run["id"])
        assert reloaded["result"]["failure"]["hint"]

"""Contracts added for project chat, prompt templates and manuscript import."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client(temp_home: Path):
    from papercreator.api.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def test_prompt_template_crud_and_scope(client: TestClient, project):
    created = client.post(
        "/api/prompts",
        json={
            "name": "Reviewer response",
            "description": "Answer one review point",
            "content": "Address {{comment}} using evidence from {{section}}.",
            "project_id": project.id,
        },
    )
    assert created.status_code == 200, created.text
    template = created.json()["template"]
    assert template["scope"] == "project"
    assert template["variables"] == ["comment", "section"]
    listed = client.get("/api/prompts", params={"project_id": project.id}).json()["items"]
    assert any(item["id"] == template["id"] for item in listed)

    updated = client.put(
        f"/api/prompts/{template['id']}",
        json={"name": "Review reply", "content": "Reply to {{comment}}.", "project_id": project.id},
    )
    assert updated.status_code == 200
    assert updated.json()["template"]["variables"] == ["comment"]
    assert client.delete(f"/api/prompts/{template['id']}").json()["deleted"] is True


def test_prompt_template_rejects_empty_content(client: TestClient):
    response = client.post("/api/prompts", json={"name": "Empty", "content": ""})
    assert response.status_code == 422


def test_assistant_without_model_is_actionable(client: TestClient, project):
    response = client.post(
        "/api/assistant/chat",
        json={"message": "Help revise the introduction", "project_id": project.id},
    )
    assert response.status_code in {400, 422}
    error = response.json()["error"]
    assert error["code"] in {"validation_error", "llm_configuration_error"}
    assert any(token in error["message"].lower() for token in ("provider", "model"))


def test_assistant_thread_scope_restore_and_delete(client: TestClient, project):
    from papercreator.store import assistant_chat

    created = client.post(
        "/api/assistant/threads",
        json={"project_id": project.id},
    )
    assert created.status_code == 200, created.text
    thread = created.json()["thread"]
    assistant_chat.append_exchange(
        thread["id"],
        user_text="Review the method",
        assistant_text="Check the sampling assumptions.",
        actions=[{"kind": "insert_into_section", "requires_confirmation": True, "payload": {}}],
        meta={"provider": "fixture", "model": "deterministic", "usage": {"prompt_tokens": 4}},
    )

    listed = client.get(
        "/api/assistant/threads", params={"project_id": project.id}
    ).json()["items"]
    assert [item["id"] for item in listed] == [thread["id"]]
    assert listed[0]["title"] == "Review the method"
    restored = client.get(f"/api/assistant/threads/{thread['id']}").json()
    assert [message["role"] for message in restored["messages"]] == ["user", "assistant"]
    assert restored["messages"][1]["actions"][0]["requires_confirmation"] is True
    assert restored["messages"][1]["meta"]["provider"] == "fixture"
    assert client.get("/api/assistant/threads").json()["items"] == []

    deleted = client.delete(f"/api/assistant/threads/{thread['id']}")
    assert deleted.json()["deleted"] is True
    assert client.get(f"/api/assistant/threads/{thread['id']}").status_code == 404


def test_assistant_scope_stats_export_and_guarded_maintenance(client: TestClient, project):
    from papercreator.core.db import execute
    from papercreator.store import assistant_chat

    project_thread = assistant_chat.create(project_id=project.id)
    assistant_chat.append_exchange(
        project_thread["id"],
        user_text="保留这段项目对话",
        assistant_text="This project-scoped answer must remain isolated.",
        actions=[],
        meta={"provider": "fixture", "model": "deterministic"},
    )
    workbench_thread = assistant_chat.create()
    assistant_chat.append_exchange(
        workbench_thread["id"],
        user_text="Workbench question",
        assistant_text="Workbench answer",
        actions=[],
        meta={},
    )

    listing = client.get(
        "/api/assistant/threads", params={"project_id": project.id}
    ).json()
    assert listing["stats"]["thread_count"] == 1
    assert listing["stats"]["message_count"] == 2
    assert listing["items"][0]["message_count"] == 2
    assert listing["items"][0]["character_count"] > 0
    assert listing["items"][0]["estimated_bytes"] >= listing["items"][0]["character_count"]

    exported = client.get(
        "/api/assistant/threads/export", params={"project_id": project.id}
    )
    assert exported.status_code == 200, exported.text
    payload = exported.json()
    assert payload["format"] == "papercreator.assistant_conversations"
    assert payload["format_version"] == 1
    assert payload["scope"]["project_id"] == project.id
    assert [item["id"] for item in payload["threads"]] == [project_thread["id"]]
    assert [item["role"] for item in payload["threads"][0]["messages"]] == [
        "user", "assistant",
    ]
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload

    execute(
        "UPDATE assistant_threads SET updated_at=? WHERE id=?",
        ("2020-01-01T00:00:00+00:00", project_thread["id"]),
    )
    preview = client.post(
        "/api/assistant/threads/maintenance/preview",
        json={"project_id": project.id, "mode": "retention", "older_than_days": 30},
    )
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["stats"]["thread_count"] == 1
    assert plan["stats"]["message_count"] == 2

    assistant_chat.append_exchange(
        project_thread["id"],
        user_text="Changed after preview",
        assistant_text="The stale preview must now be rejected.",
        actions=[],
        meta={},
    )
    stale = client.post(
        "/api/assistant/threads/maintenance/execute",
        json={
            "project_id": project.id,
            "mode": "retention",
            "cutoff": plan["cutoff"],
            "preview_token": plan["preview_token"],
            "confirm": True,
        },
    )
    assert stale.status_code in {400, 422}
    assert assistant_chat.get(project_thread["id"]) is not None

    all_preview = client.post(
        "/api/assistant/threads/maintenance/preview",
        json={"project_id": project.id, "mode": "all"},
    ).json()
    refused = client.post(
        "/api/assistant/threads/maintenance/execute",
        json={
            "project_id": project.id,
            "mode": "all",
            "cutoff": None,
            "preview_token": all_preview["preview_token"],
            "confirm": False,
        },
    )
    assert refused.status_code in {400, 422}
    deleted = client.post(
        "/api/assistant/threads/maintenance/execute",
        json={
            "project_id": project.id,
            "mode": "all",
            "cutoff": None,
            "preview_token": all_preview["preview_token"],
            "confirm": True,
        },
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted_threads"] == 1
    assert assistant_chat.get(project_thread["id"]) is None
    assert assistant_chat.get(workbench_thread["id"]) is not None
    assistant_chat.delete(workbench_thread["id"])


def test_assistant_archive_import_is_atomic_scoped_and_idempotent(
    client: TestClient, project
):
    from papercreator.store import assistant_chat

    user_text = "Restore this exact question"
    assistant_text = "Restore this exact answer"
    source = assistant_chat.create(project_id=project.id, title="Portable archive")
    assistant_chat.append_exchange(
        source["id"],
        user_text=user_text,
        assistant_text=assistant_text,
        actions=[{"kind": "open_search", "requires_confirmation": False, "payload": {}}],
        meta={"provider": "fixture", "usage": {"prompt_tokens": 2}},
    )
    archive = client.get(
        "/api/assistant/threads/export", params={"project_id": project.id}
    ).json()

    same_scope = client.post(
        "/api/assistant/threads/import/preview",
        json={"project_id": project.id, "archive": archive},
    ).json()
    assert same_scope["stats"]["new_threads"] == 0
    assert same_scope["stats"]["already_imported_threads"] == 1
    same_scope_result = client.post(
        "/api/assistant/threads/import/execute",
        json={
            "project_id": project.id, "archive": archive,
            "preview_token": same_scope["preview_token"], "confirm": True,
        },
    ).json()
    assert same_scope_result["imported_threads"] == 0
    assert same_scope_result["skipped_threads"] == 1
    assert len(client.get(
        "/api/assistant/threads", params={"project_id": project.id}
    ).json()["items"]) == 1

    preview_response = client.post(
        "/api/assistant/threads/import/preview",
        json={"project_id": "", "archive": archive},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["target_scope"]["kind"] == "workbench"
    assert preview["source_scope"]["project_id"] == project.id
    assert preview["stats"] == {
        "thread_count": 1,
        "message_count": 2,
        "character_count": len(user_text) + len(assistant_text),
        "estimated_bytes": preview["stats"]["estimated_bytes"],
        "new_threads": 1,
        "already_imported_threads": 0,
    }

    refused = client.post(
        "/api/assistant/threads/import/execute",
        json={
            "project_id": "", "archive": archive,
            "preview_token": preview["preview_token"], "confirm": False,
        },
    )
    assert refused.status_code in {400, 422}
    assert client.get("/api/assistant/threads").json()["items"] == []

    imported_response = client.post(
        "/api/assistant/threads/import/execute",
        json={
            "project_id": "", "archive": archive,
            "preview_token": preview["preview_token"], "confirm": True,
        },
    )
    assert imported_response.status_code == 200, imported_response.text
    imported = imported_response.json()
    assert imported["imported_threads"] == 1
    assert imported["imported_messages"] == 2
    assert imported["skipped_threads"] == 0
    assert imported["thread_ids"][0] != source["id"]
    restored = client.get(
        f"/api/assistant/threads/{imported['thread_ids'][0]}"
    ).json()
    assert [item["content"] for item in restored["messages"]] == [
        user_text, assistant_text,
    ]
    assert restored["messages"][1]["actions"][0]["kind"] == "open_search"
    assert restored["messages"][1]["meta"]["provider"] == "fixture"

    repeated_preview = client.post(
        "/api/assistant/threads/import/preview",
        json={"project_id": "", "archive": archive},
    ).json()
    assert repeated_preview["stats"]["new_threads"] == 0
    assert repeated_preview["stats"]["already_imported_threads"] == 1
    repeated = client.post(
        "/api/assistant/threads/import/execute",
        json={
            "project_id": "", "archive": archive,
            "preview_token": repeated_preview["preview_token"], "confirm": True,
        },
    ).json()
    assert repeated["imported_threads"] == 0
    assert repeated["skipped_threads"] == 1

    changed = json.loads(json.dumps(archive))
    changed["threads"][0]["messages"][1]["content"] += " Changed upstream."
    changed_preview = client.post(
        "/api/assistant/threads/import/preview",
        json={"project_id": "", "archive": changed},
    ).json()
    assert changed_preview["stats"]["new_threads"] == 1
    changed_result = client.post(
        "/api/assistant/threads/import/execute",
        json={
            "project_id": "", "archive": changed,
            "preview_token": changed_preview["preview_token"], "confirm": True,
        },
    ).json()
    assert changed_result["imported_threads"] == 1
    assert len(client.get("/api/assistant/threads").json()["items"]) == 2

    invalid = json.loads(json.dumps(archive))
    invalid["threads"].append({
        "id": "broken", "title": "Broken", "messages": [{"role": "system", "content": "x"}],
    })
    rejected = client.post(
        "/api/assistant/threads/import/preview",
        json={"project_id": "", "archive": invalid},
    )
    assert rejected.status_code in {400, 422}
    assert len(client.get("/api/assistant/threads").json()["items"]) == 2

    for item in client.get("/api/assistant/threads").json()["items"]:
        assistant_chat.delete(item["id"])
    assistant_chat.delete(source["id"])


def test_assistant_message_redaction_is_guarded_and_removes_sensitive_payload(
    client: TestClient, project
):
    from papercreator.core.db import execute
    from papercreator.store import assistant_chat

    secret = "unpublished-sensitive-result-42"
    thread = assistant_chat.create(project_id=project.id, title="Sensitive exchange")
    assistant_chat.append_exchange(
        thread["id"],
        user_text=secret,
        assistant_text="A response with an action",
        actions=[{
            "kind": "insert_into_section", "requires_confirmation": True,
            "payload": {"draft": secret},
        }],
        meta={"provider": "fixture", "private_context": secret},
    )
    stored = client.get(f"/api/assistant/threads/{thread['id']}").json()["messages"]
    target = stored[1]
    preview = client.post(
        f"/api/assistant/messages/{target['id']}/redaction/preview"
    ).json()
    assert preview["actions_count"] == 1
    assert preview["has_meta"] is True

    execute(
        "UPDATE assistant_messages SET content=? WHERE id=?",
        ("Changed after preview", target["id"]),
    )
    stale = client.post(
        f"/api/assistant/messages/{target['id']}/redaction/execute",
        json={"preview_token": preview["preview_token"], "reason": "privacy", "confirm": True},
    )
    assert stale.status_code in {400, 422}
    assert client.get(f"/api/assistant/threads/{thread['id']}").json()["messages"][1][
        "content"
    ] == "Changed after preview"

    fresh = client.post(
        f"/api/assistant/messages/{target['id']}/redaction/preview"
    ).json()
    redacted = client.post(
        f"/api/assistant/messages/{target['id']}/redaction/execute",
        json={"preview_token": fresh["preview_token"], "reason": "privacy", "confirm": True},
    )
    assert redacted.status_code == 200, redacted.text
    restored = client.get(f"/api/assistant/threads/{thread['id']}").json()["messages"]
    assert restored[0]["content"] == secret
    assert restored[1]["content"] == "[Content removed by user]"
    assert restored[1]["actions"] == []
    assert set(restored[1]["meta"]) == {"redaction"}
    assert restored[1]["meta"]["redaction"]["reason"] == "privacy"
    assert restored[1]["meta"]["redaction"]["actions_removed"] == 1
    assert restored[1]["meta"]["redaction"]["meta_removed"] is True
    assert secret not in json.dumps(restored[1], ensure_ascii=False)
    exported = client.get(
        "/api/assistant/threads/export", params={"project_id": project.id}
    ).text
    assert "private_context" not in exported
    assert "A response with an action" not in exported

    repeated = client.post(
        f"/api/assistant/messages/{target['id']}/redaction/preview"
    )
    assert repeated.status_code in {400, 422}
    assistant_chat.delete(thread["id"])


def test_assistant_project_delete_cascades_threads_and_messages(temp_home: Path):
    from papercreator.core.db import query_one
    from papercreator.store import assistant_chat
    from papercreator.store import projects as projects_store

    disposable = projects_store.create(
        title="Assistant cascade project",
        idea="Verify project-owned conversation cleanup.",
        git_enabled=False,
    )
    thread = assistant_chat.create(project_id=disposable.id)
    assistant_chat.append_exchange(
        thread["id"],
        user_text="Cascade me",
        assistant_text="This exchange belongs only to the disposable project.",
        actions=[],
        meta={},
    )
    projects_store.delete(disposable.id, remove_files=True)
    assert query_one("SELECT 1 FROM assistant_threads WHERE id=?", (thread["id"],)) is None
    assert query_one(
        "SELECT 1 FROM assistant_messages WHERE thread_id=?", (thread["id"],)
    ) is None


def test_assistant_retention_setting_defaults_off_and_persists(client: TestClient):
    current = client.get("/api/settings").json()
    assert current["assistant"]["retention_days"] == 0
    updated = client.patch("/api/settings", json={"assistant": {"retention_days": 90}})
    assert updated.status_code == 200, updated.text
    assert updated.json()["assistant"]["retention_days"] == 90
    reset = client.patch("/api/settings", json={"assistant": {"retention_days": 0}})
    assert reset.status_code == 200


def test_translation_providers_and_offline_glossary(client: TestClient):
    providers = client.get("/api/writing/translation/providers")
    assert providers.status_code == 200
    ids = {item["id"] for item in providers.json()["items"]}
    assert {"builtin-glossary", "mymemory", "llm"} <= ids
    translated = client.post(
        "/api/writing/translate",
        json={
            "text": "causal inference",
            "source": "en",
            "target": "zh-CN",
            "provider": "builtin-glossary",
        },
    )
    assert translated.status_code == 200
    assert translated.json()["text"] == "因果推断"
    missing = client.post(
        "/api/writing/translate",
        json={
            "text": "a term not in the offline glossary",
            "source": "en",
            "target": "zh-CN",
            "provider": "builtin-glossary",
        },
    ).json()
    assert missing["found"] is False
    assert missing["text"] == ""


def test_mymemory_chunking_retries_and_preserves_local_blocks():
    from papercreator.writing.translation import mymemory_translate

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        source = request.url.params["q"]
        return httpx.Response(200, json={"responseData": {"translatedText": f"译:{source}"}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
            return await mymemory_translate(
                "First sentence.\n\n```python\nprint('local')\n```\n\nSecond sentence.",
                "en",
                "zh-CN",
                client=mock_client,
                min_interval_s=0,
                sleep=lambda _seconds: asyncio.sleep(0),
            )

    result = asyncio.run(run())
    assert result["retries"] == 1
    assert result["requests"] == result["chunks"] + 1
    assert "```python\nprint('local')\n```" in result["text"]
    assert "\n\n" in result["text"]
    assert calls == result["requests"]


def test_mymemory_shared_job_throttle_spans_sections():
    from papercreator.writing.translation import mymemory_translate

    waits: list[float] = []
    state: dict[str, float] = {"last_request_at": 0.0}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"responseStatus": 200, "responseData": {"translatedText": request.url.params["q"]}},
        )

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_client:
            await mymemory_translate(
                "Section one.", "en", "zh-CN", client=mock_client,
                request_state=state, min_interval_s=0.2, sleep=fake_sleep,
            )
            await mymemory_translate(
                "Section two.", "en", "zh-CN", client=mock_client,
                request_state=state, min_interval_s=0.2, sleep=fake_sleep,
            )

    asyncio.run(run())
    assert sum(waits) > 0.15


def test_translation_job_is_preview_only_then_applies_atomically(
    client: TestClient, project, monkeypatch: pytest.MonkeyPatch
):
    from papercreator.core.jobs import manager
    from papercreator.store import documents as documents_store
    from papercreator.store import snapshots as snapshots_store
    from papercreator.writing import translation

    document = documents_store.primary_document(project.id)
    first = documents_store.create_section(
        document.id,
        key="translation-one",
        title="Translation one",
        content="A public academic paragraph.",
    )
    second = documents_store.create_section(
        document.id,
        key="translation-two",
        title="Translation two",
        content="A second public paragraph.",
    )
    documents_store.flush_document_to_disk(document.id)

    async def fake_translate(text: str, source: str, target: str, **kwargs):
        checkpoint = kwargs.get("checkpoint")
        progress = kwargs.get("progress")
        if checkpoint:
            checkpoint()
        if progress:
            progress(1, 1, "translated chunk 1/1")
        return {
            "text": f"译文：{text}",
            "provider": "mymemory",
            "source": source,
            "target": target,
            "requests": 1,
            "retries": 0,
            "note": "fixture",
        }

    monkeypatch.setattr(translation, "mymemory_translate", fake_translate)
    rejected = client.post(
        "/api/writing/translation/jobs",
        json={"project_id": project.id, "provider": "mymemory"},
    )
    assert rejected.status_code in {400, 422}

    accepted = client.post(
        "/api/writing/translation/jobs",
        json={
            "project_id": project.id,
            "section_keys": [first.key, second.key],
            "provider": "mymemory",
            "source": "en",
            "target": "zh-CN",
            "confirm_external": True,
        },
    )
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]
    job = manager.wait(job_id, 5)
    assert job["status"] == "done", job
    assert job["result"]["preview_only"] is True
    assert job["result"]["section_count"] == 2
    assert documents_store.require_section(first.id).content_zh == ""
    assert documents_store.require_section(second.id).content_zh == ""

    applied = client.post(
        f"/api/writing/translation/jobs/{job_id}/apply", json={"confirm": True}
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["sections_applied"] == 2
    assert applied.json()["already_applied"] is False
    assert documents_store.require_section(first.id).content_zh.startswith("译文：")
    assert documents_store.require_section(second.id).content_zh.startswith("译文：")
    assert snapshots_store.get(applied.json()["snapshot_id"]) is not None
    repeated = client.post(
        f"/api/writing/translation/jobs/{job_id}/apply", json={"confirm": True}
    )
    assert repeated.json()["already_applied"] is True


def test_translation_preview_rejects_changed_source(
    client: TestClient, project, monkeypatch: pytest.MonkeyPatch
):
    from papercreator.core.jobs import manager
    from papercreator.store import documents as documents_store
    from papercreator.writing import translation

    document = documents_store.primary_document(project.id)
    section = documents_store.create_section(
        document.id, key="stale-translation", title="Stale", content="Original source."
    )
    documents_store.flush_document_to_disk(document.id)

    async def fake_translate(text: str, source: str, target: str, **_kwargs):
        return {"text": "预览译文", "provider": "mymemory", "source": source, "target": target}

    monkeypatch.setattr(translation, "mymemory_translate", fake_translate)
    accepted = client.post(
        "/api/writing/translation/jobs",
        json={
            "project_id": project.id,
            "section_keys": [section.key],
            "provider": "mymemory",
            "confirm_external": True,
        },
    ).json()
    assert manager.wait(accepted["job_id"], 5)["status"] == "done"
    documents_store.update_section(section.id, content="Changed after preview.")
    stale = client.post(
        f"/api/writing/translation/jobs/{accepted['job_id']}/apply",
        json={"confirm": True},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "translation_preview_stale"
    assert documents_store.require_section(section.id).content_zh == ""


def test_translation_job_cancellation_leaves_manuscript_unchanged(
    client: TestClient, project, monkeypatch: pytest.MonkeyPatch
):
    from papercreator.core.jobs import manager
    from papercreator.store import documents as documents_store
    from papercreator.writing import translation

    document = documents_store.primary_document(project.id)
    section = documents_store.create_section(
        document.id, key="cancel-translation", title="Cancel", content="Public source."
    )
    documents_store.flush_document_to_disk(document.id)

    async def slow_translate(text: str, source: str, target: str, **kwargs):
        for _ in range(100):
            checkpoint = kwargs.get("checkpoint")
            if checkpoint:
                checkpoint()
            await asyncio.sleep(0.01)
        return {"text": "should not complete", "provider": "mymemory"}

    monkeypatch.setattr(translation, "mymemory_translate", slow_translate)
    accepted = client.post(
        "/api/writing/translation/jobs",
        json={
            "project_id": project.id,
            "section_keys": [section.key],
            "provider": "mymemory",
            "confirm_external": True,
        },
    ).json()
    deadline = time.time() + 2
    while time.time() < deadline and manager.get(accepted["job_id"])["status"] == "queued":
        time.sleep(0.01)
    assert manager.cancel(accepted["job_id"]) is True
    assert manager.wait(accepted["job_id"], 5)["status"] == "cancelled"
    assert documents_store.require_section(section.id).content_zh == ""


def test_structure_template_catalogue_is_complete_and_distinguishes_layouts(client: TestClient):
    response = client.get("/api/writing/templates")
    assert response.status_code == 200
    items = response.json()["items"]
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids))
    assert {"sci-imrad", "ssci-empirical", "conference-full", "systematic-review", "research-poster"} <= set(ids)
    for item in items:
        assert item["section_count"] == len(item["sections"])
        assert item["total_words"] > 0
        assert item["category"]
        assert item["source_kind"] == "built-in-structure"
        assert "not an official venue" in item["license_note"]


def test_markdown_import_preview_and_append(client: TestClient, project, tmp_path: Path):
    source = tmp_path / "draft.md"
    source.write_text(
        "# Abstract\n\nA concise abstract.\n\n# Introduction\n\nThe research problem and motivation.\n",
        encoding="utf-8",
    )
    preview_response = client.post(
        "/api/writing/import/preview",
        json={"source_path": str(source), "project_id": project.id},
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["section_count"] == 2
    assert "content" not in preview["sections"][0]
    assert "content_preview" in preview["sections"][0]

    applied = client.post(
        f"/api/writing/{project.id}/import",
        json={
            "source_path": str(source),
            "source_sha256": preview["source_sha256"],
            "mode": "append",
            "selected_indices": [0, 1],
        },
    )
    assert applied.status_code == 200, applied.text
    payload = applied.json()
    assert payload["created_count"] == 2
    assert Path(payload["managed_source"]).is_file()
    document = client.get(f"/api/writing/{project.id}/document").json()["document"]
    assert [section["key"] for section in document["sections"]] == ["abstract", "introduction"]


def test_scanned_pdf_import_returns_ocr_preview_and_applies_mocked_local_ocr(
    client: TestClient, project, tmp_path: Path, monkeypatch
):
    from pypdf import PdfWriter
    from papercreator.importers import local_ocr

    source = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with source.open("wb") as handle:
        writer.write(handle)

    plain = client.post(
        "/api/writing/import/preview",
        json={"source_path": str(source), "project_id": project.id},
    )
    assert plain.status_code == 200, plain.text
    assert plain.json()["requires_ocr"] is True
    assert plain.json()["section_count"] == 0
    assert "available" in plain.json()["ocr_capabilities"]

    monkeypatch.setattr(local_ocr, "ocr_pdf_pages", lambda *_args, **_kwargs: {
        "texts": {0: "Abstract\n\nOffline OCR restores this scanned manuscript."},
        "warnings": [],
        "engine": "tesseract",
        "renderer": "fixture",
        "languages": "eng",
        "dpi": 200,
    })
    preview = client.post(
        "/api/writing/import/preview",
        json={
            "source_path": str(source), "project_id": project.id,
            "use_ocr": True, "ocr_languages": "eng", "ocr_max_pages": 10,
        },
    )
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert payload["requires_ocr"] is False
    assert payload["ocr_used"] is True
    assert payload["method"] == "pypdf+tesseract-ocr"
    assert payload["section_count"] == 1

    applied = client.post(
        f"/api/writing/{project.id}/import",
        json={
            "source_path": str(source), "source_sha256": payload["source_sha256"],
            "mode": "append", "selected_indices": [0], "use_ocr": True,
            "ocr_languages": "eng", "ocr_max_pages": 10,
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["created_count"] == 1


def test_disjoint_manuscript_merge_api_requires_confirmation_and_snapshots(
    client: TestClient, project
):
    from pathlib import Path
    from papercreator.store import documents as documents_store

    document = documents_store.primary_document(project.id)
    first = documents_store.create_section(
        document.id, key="api-merge-first", title="First", content="First baseline."
    )
    second = documents_store.create_section(
        document.id, key="api-merge-second", title="Second", content="Second baseline."
    )
    flushed = documents_store.flush_document_to_disk(document.id)
    documents_store.update_section(first.id, content="First changed in DB.")
    second_file = Path(flushed["path"]) / documents_store.section_filename(
        second, document.format
    )
    second_file.write_text("# Second\n\nSecond changed on disk.\n", encoding="utf-8")
    status = client.get(f"/api/writing/{project.id}/sync-status").json()
    assert status["can_auto_merge"] is True

    refused = client.post(
        f"/api/writing/{project.id}/merge-disjoint",
        json={"preview_token": status["merge_preview_token"], "confirm": False},
    )
    assert refused.status_code in {400, 422}
    assert documents_store.get_section_by_key(document.id, "api-merge-second").content == "Second baseline."

    merged = client.post(
        f"/api/writing/{project.id}/merge-disjoint",
        json={"preview_token": status["merge_preview_token"], "confirm": True},
    )
    assert merged.status_code == 200, merged.text
    payload = merged.json()
    assert payload["safety_snapshot"]["id"]
    assert payload["merged_from_database"] == ["api-merge-first"]
    assert payload["merged_from_disk"] == ["api-merge-second"]
    assert payload["sync"]["state"] == "in_sync"


def test_replace_import_requires_confirmation_and_creates_snapshot(
    client: TestClient, project, tmp_path: Path
):
    source = tmp_path / "replacement.txt"
    source.write_text("A replacement manuscript without headings.", encoding="utf-8")
    preview = client.post(
        "/api/writing/import/preview",
        json={"source_path": str(source), "project_id": project.id},
    ).json()
    body = {
        "source_path": str(source),
        "source_sha256": preview["source_sha256"],
        "mode": "replace",
        "selected_indices": [0],
    }
    refused = client.post(f"/api/writing/{project.id}/import", json=body)
    assert refused.status_code == 409
    applied = client.post(
        f"/api/writing/{project.id}/import",
        json={**body, "confirm_replace": True},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["safety_snapshot"]
    document = client.get(f"/api/writing/{project.id}/document").json()["document"]
    assert len(document["sections"]) == 1


def test_venue_template_zip_is_inspected_and_imported(client: TestClient, project, tmp_path: Path):
    archive = tmp_path / "venue.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("template/main.tex", "\\documentclass{venue}\\begin{document}\\end{document}")
        bundle.writestr("template/venue.cls", "% class")
        bundle.writestr("LICENSE", "LPPL 1.3c")
    preview = client.post(
        "/api/writing/venue-template/preview", json={"source_path": str(archive)}
    )
    assert preview.status_code == 200, preview.text
    inspected = preview.json()
    assert inspected["latex"]["class_files"] == 1
    assert inspected["license_candidates"] == ["LICENSE"]
    refused = client.post(
        f"/api/writing/{project.id}/venue-template",
        json={
            "source_path": str(archive),
            "source_sha256": inspected["source_sha256"],
            "name": "Test Venue",
        },
    )
    assert refused.status_code == 409
    imported = client.post(
        f"/api/writing/{project.id}/venue-template",
        json={
            "source_path": str(archive),
            "source_sha256": inspected["source_sha256"],
            "name": "Test Venue",
            "source_url": "https://example.test/template",
            "license_name": "LPPL 1.3c",
            "confirm_license": True,
        },
    )
    assert imported.status_code == 200, imported.text
    assert (Path(imported.json()["template"]["path"]) / "template" / "main.tex").is_file()


def test_venue_template_rejects_path_traversal(client: TestClient, tmp_path: Path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.tex", "unsafe")
    response = client.post(
        "/api/writing/venue-template/preview", json={"source_path": str(archive)}
    )
    assert response.status_code == 422

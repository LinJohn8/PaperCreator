"""Writing routes: the editor's backend.

Section edits go through :mod:`writing.manuscript` rather than the store directly,
so every save also refreshes the section's derived citation list and flushes the
on-disk mirror that git tracks. That keeps the export bibliography and the git
history consistent with what is in the editor.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ...core.db import dumps, execute, transaction
from ...core.errors import ConflictError, NotFoundError, ValidationError
from ...core.jobs import JobContext, manager
from ...core.logging_setup import get_logger
from ...core.util import sha256_text, utc_now_iso
from ...store import documents as documents_store
from ...store import projects as projects_store
from ...writing import manuscript, templates

log = get_logger(__name__)
router = APIRouter(prefix="/api/writing", tags=["writing"])


@router.get("/templates")
def list_templates() -> dict[str, Any]:
    return {"items": templates.list_templates()}


class ManuscriptImportPreview(BaseModel):
    source_path: str
    project_id: str = ""
    use_ocr: bool = False
    ocr_languages: str = Field(default="eng", min_length=1, max_length=64)
    ocr_max_pages: int = Field(default=50, ge=1, le=200)


@router.post("/import/preview")
def preview_manuscript_import(body: ManuscriptImportPreview) -> dict[str, Any]:
    from ...writing import manuscript_import

    return manuscript_import.preview(
        body.source_path,
        project_id=body.project_id,
        use_ocr=body.use_ocr,
        ocr_languages=body.ocr_languages,
        ocr_max_pages=body.ocr_max_pages,
    )


@router.get("/import/ocr-capabilities")
def manuscript_ocr_capabilities() -> dict[str, Any]:
    from ...importers.local_ocr import capabilities

    return capabilities()


class ManuscriptImportApply(BaseModel):
    source_path: str
    source_sha256: str
    mode: str = Field(default="append", pattern="^(append|replace)$")
    selected_indices: list[int] | None = None
    confirm_replace: bool = False
    use_ocr: bool = False
    ocr_languages: str = Field(default="eng", min_length=1, max_length=64)
    ocr_max_pages: int = Field(default=50, ge=1, le=200)


class ManuscriptMergeExecute(BaseModel):
    preview_token: str = Field(min_length=64, max_length=64)
    confirm: bool = False


@router.post("/{project_id}/import")
def apply_manuscript_import(project_id: str, body: ManuscriptImportApply) -> dict[str, Any]:
    from ...writing import manuscript_import

    return manuscript_import.apply(project_id, **body.model_dump())


class VenueTemplatePreview(BaseModel):
    source_path: str


@router.post("/venue-template/preview")
def preview_venue_template(body: VenueTemplatePreview) -> dict[str, Any]:
    from ...writing import venue_templates

    return venue_templates.preview(body.source_path)


class VenueTemplateApply(BaseModel):
    source_path: str
    source_sha256: str
    name: str = Field(min_length=1, max_length=160)
    source_url: str = Field(default="", max_length=2_000)
    license_name: str = Field(default="", max_length=500)
    confirm_license: bool = False


@router.post("/{project_id}/venue-template")
def import_venue_template(project_id: str, body: VenueTemplateApply) -> dict[str, Any]:
    from ...writing import venue_templates

    return venue_templates.apply(project_id, **body.model_dump())


@router.get("/translation/providers")
def translation_providers() -> dict[str, Any]:
    from ...core.config import get_settings

    return {
        "items": [
            {
                "id": "builtin-glossary",
                "name": "Academic terminology glossary",
                "name_zh": "内置学术术语表",
                "available": True,
                "external": False,
                "cost": "free",
                "scope": "exact terms",
            },
            {
                "id": "mymemory",
                "name": "MyMemory public translation",
                "name_zh": "MyMemory 公共翻译",
                "available": True,
                "external": True,
                "confirmation_required": True,
                "cost": "free with public-service limits",
                "scope": "selected text or background jobs up to 100,000 characters",
                "privacy": "Text is sent to api.mymemory.translated.net.",
            },
            {
                "id": "llm",
                "name": "Configured LLM",
                "name_zh": "已配置的大模型",
                "available": bool(get_settings().llm.providers),
                "external": True,
                "cost": "depends on the selected model",
                "scope": "professional contextual translation",
            },
        ]
    }


class TranslationRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    source: str = Field(default="en", pattern="^(en|zh|zh-CN)$")
    target: str = Field(default="zh-CN", pattern="^(en|zh|zh-CN)$")
    provider: str = Field(default="builtin-glossary", pattern="^(builtin-glossary|mymemory|llm)$")
    professional: bool = True
    confirm_external: bool = False


@router.post("/translate")
async def translate_text(request: TranslationRequest) -> dict[str, Any]:
    from ...writing.translation import glossary_lookup, llm_translate, mymemory_translate

    if request.source == request.target:
        raise ValidationError("source and target languages must differ")
    if request.provider == "builtin-glossary":
        return glossary_lookup(request.text, request.source, request.target)
    if request.provider == "mymemory":
        if not request.confirm_external:
            raise ValidationError(
                "MyMemory requires explicit confirmation that the text may be sent to its public service"
            )
        if len(request.text) > 10_000:
            raise ValidationError(
                "MyMemory text above 10,000 characters must use a cancellable translation job"
            )
        return await mymemory_translate(
            request.text, request.source, request.target, max_characters=10_000
        )
    return await llm_translate(request.text, request.source, request.target)


class TranslationJobRequest(BaseModel):
    text: str = Field(default="", max_length=200_000)
    project_id: str = ""
    section_keys: list[str] = Field(default_factory=list, max_length=100)
    source: str = Field(default="en", pattern="^(en|zh|zh-CN)$")
    target: str = Field(default="zh-CN", pattern="^(en|zh|zh-CN)$")
    provider: str = Field(default="mymemory", pattern="^(mymemory|llm)$")
    overwrite: bool = False
    confirm_external: bool = False


def _validate_translation_boundary(request: TranslationJobRequest) -> None:
    if request.source == request.target:
        raise ValidationError("source and target languages must differ")
    if bool(request.text.strip()) == bool(request.project_id):
        raise ValidationError(
            "translation job requires either direct text or a project_id, but not both"
        )
    if request.provider == "mymemory" and not request.confirm_external:
        raise ValidationError(
            "MyMemory requires explicit confirmation that manuscript text may be sent to its public service"
        )


@router.post("/translation/jobs", status_code=202)
def start_translation_job(request: TranslationJobRequest) -> dict[str, Any]:
    """Create a cancellable translation preview; never writes the manuscript."""
    from ...writing.translation import _chunks, llm_translate, mymemory_translate

    _validate_translation_boundary(request)
    mode = "project" if request.project_id else "text"
    section_snapshots: list[dict[str, Any]] = []
    if mode == "project":
        projects_store.require(request.project_id)
        document = documents_store.primary_document(request.project_id)
        sections = documents_store.list_sections(document.id)
        by_key = {section.key: section for section in sections}
        if request.section_keys:
            missing = [key for key in request.section_keys if key not in by_key]
            if missing:
                raise ValidationError(
                    f"translation section(s) not found: {', '.join(missing)}"
                )
            sections = [by_key[key] for key in request.section_keys]
        sections = [
            section for section in sections
            if section.content.strip() and (request.overwrite or not section.content_zh.strip())
        ]
        if not sections:
            raise ValidationError("no manuscript sections need translation")
        for section in sections:
            section_snapshots.append({
                "key": section.key,
                "title": section.title,
                "title_zh": section.title_zh,
                "text": section.content,
                "source_sha256": sha256_text(section.content),
                "target_sha256": sha256_text(section.content_zh),
            })
        total_characters = sum(len(item["text"]) for item in section_snapshots)
    else:
        total_characters = len(request.text)
        if not request.text.strip():
            raise ValidationError("translation text is empty")
    maximum = 100_000 if request.provider == "mymemory" else 500_000
    if total_characters > maximum:
        raise ValidationError(
            f"{request.provider} translation jobs are limited to {maximum:,} source characters"
        )
    if request.provider == "mymemory":
        request_estimate = sum(
            len(_chunks(item["text"])) for item in section_snapshots
        ) if mode == "project" else len(_chunks(request.text))
        if request_estimate > 250:
            raise ValidationError(
                f"MyMemory translation would require {request_estimate} requests; the per-job limit is 250"
            )

    async def run(ctx: JobContext) -> dict[str, Any]:
        public_client = (
            httpx.AsyncClient(timeout=30.0, follow_redirects=False)
            if request.provider == "mymemory"
            else None
        )
        request_state: dict[str, float] = {"last_request_at": 0.0}

        async def translate_one(
            text: str,
            *,
            section_index: int = 0,
            section_count: int = 1,
        ) -> dict[str, Any]:
            if request.provider == "mymemory":
                return await mymemory_translate(
                    text,
                    request.source,
                    request.target,
                    client=public_client,
                    checkpoint=ctx.raise_if_cancelled,
                    request_state=request_state,
                    progress=lambda done, total, message: ctx.progress(
                        (section_index + done / max(1, total)) / max(1, section_count),
                        message,
                        section=section_index + 1,
                        sections=section_count,
                    ),
                )
            ctx.raise_if_cancelled()
            result = await llm_translate(text, request.source, request.target)
            ctx.raise_if_cancelled()
            return result

        try:
            if mode == "text":
                translated = await translate_one(request.text)
                return {
                    "mode": "text",
                    **translated,
                    "source_sha256": sha256_text(request.text),
                    "source_characters": len(request.text),
                    "preview_only": True,
                }

            translated_sections: list[dict[str, Any]] = []
            count = len(section_snapshots)
            total_requests = 0
            total_retries = 0
            for index, section in enumerate(section_snapshots):
                ctx.raise_if_cancelled()
                ctx.progress(index / count, f"translating section {index + 1}/{count}")
                translated = await translate_one(
                    section["text"], section_index=index, section_count=count
                )
                translated_sections.append({
                    "key": section["key"],
                    "title": section["title"],
                    "title_zh": section["title_zh"],
                    "source_sha256": section["source_sha256"],
                    "target_sha256": section["target_sha256"],
                    "source_characters": len(section["text"]),
                    "text": translated["text"],
                    "translated_characters": len(translated["text"]),
                })
                total_requests += int(translated.get("requests") or 0)
                total_retries += int(translated.get("retries") or 0)
            return {
                "mode": "project",
                "project_id": request.project_id,
                "provider": request.provider,
                "source": request.source,
                "target": request.target,
                "overwrite": request.overwrite,
                "sections": translated_sections,
                "section_count": count,
                "source_characters": total_characters,
                "translated_characters": sum(item["translated_characters"] for item in translated_sections),
                "requests": total_requests,
                "retries": total_retries,
                "preview_only": True,
                "note": (
                    "Public external service; source text was sent to api.mymemory.translated.net."
                    if request.provider == "mymemory"
                    else "Generated by the configured LLM provider; verify specialist terminology."
                ),
            }
        finally:
            if public_client is not None:
                await public_client.aclose()

    def worker(ctx: JobContext) -> dict[str, Any]:
        return asyncio.run(run(ctx))

    safe_payload = {
        "mode": mode,
        "project_id": request.project_id or None,
        "section_keys": [item["key"] for item in section_snapshots],
        "source_sha256": sha256_text(request.text) if mode == "text" else None,
        "source_fingerprints": {
            item["key"]: item["source_sha256"] for item in section_snapshots
        },
        "source_characters": total_characters,
        "provider": request.provider,
        "source": request.source,
        "target": request.target,
        "overwrite": request.overwrite,
        "external_confirmed": bool(request.confirm_external),
        "preview_only": True,
        "request_limit": 250 if request.provider == "mymemory" else None,
    }
    handle = manager.submit(
        "translation",
        worker,
        payload=safe_payload,
        project_id=request.project_id or None,
    )
    return {
        "job_id": handle.id,
        "status": "queued",
        "mode": mode,
        "sections": len(section_snapshots),
        "source_characters": total_characters,
        "preview_only": True,
    }


class TranslationApply(BaseModel):
    confirm: bool = False


@router.post("/translation/jobs/{job_id}/apply")
def apply_translation_job(job_id: str, body: TranslationApply) -> dict[str, Any]:
    """Apply a completed project preview once, after fingerprint verification."""
    from ...core import events
    from ...store import snapshots as snapshots_store

    if not body.confirm:
        raise ValidationError("applying a translation preview requires confirm=true")
    job = manager.get(job_id)
    if job is None:
        raise NotFoundError(f"translation job {job_id} not found")
    if job["kind"] != "translation" or job["status"] != "done":
        raise ConflictError("translation job is not complete")
    result = job.get("result") or {}
    if result.get("mode") != "project" or not result.get("project_id"):
        raise ValidationError("direct-text translation previews cannot be applied to a project")
    if result.get("applied_at"):
        return {**(result.get("apply") or {}), "already_applied": True}
    project_id = str(result["project_id"])
    if str(job.get("project_id") or "") != project_id:
        raise ConflictError("translation job project scope is inconsistent")
    projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    sections = {section.key: section for section in documents_store.list_sections(document.id)}
    conflicts: list[dict[str, str]] = []
    for translated in result.get("sections") or []:
        section = sections.get(str(translated.get("key") or ""))
        if section is None:
            conflicts.append({"key": str(translated.get("key") or ""), "reason": "section_missing"})
            continue
        if sha256_text(section.content) != translated.get("source_sha256"):
            conflicts.append({"key": section.key, "reason": "source_changed"})
        if sha256_text(section.content_zh) != translated.get("target_sha256"):
            conflicts.append({"key": section.key, "reason": "paired_text_changed"})
    if conflicts:
        raise ConflictError(
            "manuscript changed after translation; start a new preview",
            code="translation_preview_stale",
            details={"conflicts": conflicts, "job_id": job_id},
        )
    documents_store.ensure_sync_safe(document.id, "flush")
    snapshot = snapshots_store.capture(
        project_id, label="before applying translation preview", kind="manual"
    )
    applied_at = utc_now_iso()
    with transaction():
        for translated in result.get("sections") or []:
            section = sections[str(translated["key"])]
            documents_store.update_section(section.id, content_zh=str(translated["text"]))
        flush = documents_store.flush_document_to_disk(document.id)
        apply_result = {
            "applied": True,
            "job_id": job_id,
            "project_id": project_id,
            "sections_applied": len(result.get("sections") or []),
            "snapshot_id": snapshot["id"],
            "applied_at": applied_at,
            "sync": flush.get("sync"),
        }
        stored_result = {**result, "applied_at": applied_at, "apply": apply_result}
        execute("UPDATE jobs SET result=? WHERE id=?", (dumps(stored_result), job_id))
    events.publish(
        events.DOCUMENT_UPDATED,
        {"documentId": document.id, "sections": apply_result["sections_applied"]},
        project_id=project_id,
    )
    return {**apply_result, "already_applied": False}


class ApplyTemplateRequest(BaseModel):
    template_id: str = ""
    target_words: int = 0
    replace: bool = False


@router.post("/{project_id}/template")
def apply_template(project_id: str, request: ApplyTemplateRequest) -> dict[str, Any]:
    """Create the section skeleton from a template.

    Additive by default: existing sections are kept. ``replace=true`` refuses if
    any section already has text, rather than destroying work.
    """
    document = manuscript.apply_template(
        project_id, request.template_id,
        target_words=request.target_words, replace=request.replace,
    )
    return {
        "document": document.model_dump(),
        "stats": manuscript.manuscript_stats(project_id),
    }


@router.get("/{project_id}/document")
def get_document(
    project_id: str,
    include_content: bool = Query(True, description="omit for an outline-only view"),
) -> dict[str, Any]:
    """The manuscript with its sections."""
    projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    sections = documents_store.list_sections(document.id)
    return {
        "document": {
            **document.model_dump(exclude={"sections"}),
            "sections": [
                s.model_dump() if include_content
                else s.model_dump(exclude={"content", "content_zh"})
                for s in sections
            ],
        },
        "stats": manuscript.manuscript_stats(project_id),
    }


@router.get("/{project_id}/sections/{section_key}")
def get_section(project_id: str, section_key: str) -> dict[str, Any]:
    projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    section = documents_store.get_section_by_key(document.id, section_key)
    if section is None:
        raise NotFoundError(f"section '{section_key}' not found in this project")
    return section.model_dump()


class SectionUpdate(BaseModel):
    content: str | None = None
    content_zh: str | None = None
    title: str | None = None
    title_zh: str | None = None
    status: str | None = None
    guidance: str | None = None
    target_words: int | None = None
    target_words_zh: int | None = None
    level: int | None = None


@router.patch("/{project_id}/sections/{section_key}")
def update_section(
    project_id: str, section_key: str, request: SectionUpdate
) -> dict[str, Any]:
    """Save a section. Refreshes its citation list and the on-disk file."""
    projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    section = documents_store.get_section_by_key(document.id, section_key)
    if section is None:
        raise NotFoundError(f"section '{section_key}' not found in this project")

    # Content changes route through the manuscript layer (citation sync + flush);
    # metadata-only changes go straight to the store.
    metadata = {
        k: v for k, v in request.model_dump().items()
        if v is not None and k not in ("content", "content_zh", "status")
    }
    if metadata:
        documents_store.update_section(section.id, **metadata)
    if request.content is not None or request.content_zh is not None or request.status:
        updated = manuscript.set_section_content(
            project_id, section_key,
            content=request.content, content_zh=request.content_zh,
            status=request.status or "",
        )
    else:
        updated = documents_store.require_section(section.id)
        documents_store.flush_document_to_disk(document.id)
    return {"section": updated.model_dump()}


class SectionCreate(BaseModel):
    key: str = ""
    title: str
    title_zh: str = ""
    level: int = 1
    ordering: int | None = None
    guidance: str = ""
    target_words: int = 0
    target_words_zh: int = 0
    content: str = ""


@router.post("/{project_id}/sections")
def create_section(project_id: str, request: SectionCreate) -> dict[str, Any]:
    projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    section = documents_store.create_section(
        document.id,
        key=request.key, title=request.title, title_zh=request.title_zh,
        level=request.level, ordering=request.ordering, guidance=request.guidance,
        target_words=request.target_words, target_words_zh=request.target_words_zh,
        content=request.content,
    )
    documents_store.flush_document_to_disk(document.id)
    return {"section": section.model_dump()}


@router.delete("/{project_id}/sections/{section_key}")
def delete_section(project_id: str, section_key: str) -> dict[str, Any]:
    projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    section = documents_store.get_section_by_key(document.id, section_key)
    if section is None:
        raise NotFoundError(f"section '{section_key}' not found")
    deleted = documents_store.delete_section(section.id)
    documents_store.flush_document_to_disk(document.id)
    return {"deleted": deleted, "key": section_key}


class ReorderRequest(BaseModel):
    section_keys: list[str]


@router.post("/{project_id}/reorder")
def reorder_sections(project_id: str, request: ReorderRequest) -> dict[str, Any]:
    projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    by_key = {s.key: s for s in documents_store.list_sections(document.id)}
    unknown = [k for k in request.section_keys if k not in by_key]
    if unknown:
        raise ValidationError(f"unknown section key(s): {', '.join(unknown)}")
    ordered = documents_store.reorder_sections(
        document.id, [by_key[k].id for k in request.section_keys]
    )
    documents_store.flush_document_to_disk(document.id)
    return {"sections": [s.model_dump(exclude={"content", "content_zh"}) for s in ordered]}


@router.get("/{project_id}/stats")
def stats(project_id: str) -> dict[str, Any]:
    projects_store.require(project_id)
    return manuscript.manuscript_stats(project_id)


@router.get("/{project_id}/bilingual")
def bilingual(project_id: str) -> dict[str, Any]:
    """Per-section alignment between the two language versions."""
    return manuscript.bilingual_status(project_id)


@router.post("/{project_id}/swap-languages")
def swap_languages(project_id: str) -> dict[str, Any]:
    """Exchange primary and paired language across the whole manuscript."""
    return manuscript.swap_languages(project_id)


@router.get("/{project_id}/assembled")
def assembled(
    project_id: str,
    language: str = Query("primary", pattern="^(primary|paired)$"),
) -> dict[str, Any]:
    """The whole manuscript as one text, for preview and word counting."""
    result = manuscript.assemble(project_id, language=language)
    return {
        "project_id": project_id,
        "language": language,
        "text": result["text"],
        "word_count": result["word_count"],
        "blocks": [
            {k: v for k, v in block.items() if k != "text"}
            for block in result["blocks"]
        ],
        "cited_papers": len(result["cited_papers"]),
        "papers_available": len(result["papers"]),
    }


@router.post("/{project_id}/bibliography")
def regenerate_bibliography(
    project_id: str, cited_only: bool = Query(True)
) -> dict[str, Any]:
    """Write ``references/references.bib`` from the cited papers."""
    return manuscript.regenerate_bibliography(project_id, cited_only=cited_only)


@router.post("/{project_id}/flush")
def flush_to_disk(
    project_id: str,
    force: bool = Query(
        False, description="overwrite changed disk files after preserving a backup"
    ),
) -> dict[str, Any]:
    """Write DB text to files, refusing unacknowledged external edits."""
    projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    return documents_store.flush_document_to_disk(document.id, force=force)


@router.post("/{project_id}/reindex")
def reindex_from_disk(
    project_id: str,
    force: bool = Query(
        False, description="overwrite changed DB text after preserving recovery data"
    ),
) -> dict[str, Any]:
    """Re-read the manuscript files into the database.

    An explicit "disk is now the truth" operation, for after a git checkout or an
    external edit. Sections present in the database but absent on disk are left
    untouched rather than deleted.
    """
    projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    before = documents_store.sync_status(document.id)
    safety_snapshot: dict[str, Any] | None = None
    if force:
        from ...store import snapshots as snapshots_store

        safety_snapshot = snapshots_store.capture(
            project_id,
            label="before resolving manuscript conflict from files",
            kind="manual",
        )
    result = documents_store.reindex_from_disk(document.id, force=force)
    return {
        **result,
        "safety_snapshot": safety_snapshot,
        "warning": "on-disk files overwrote the database for the sections found; "
                   "sections not present on disk were left unchanged",
    }


@router.get("/{project_id}/sync-status")
def manuscript_sync_status(project_id: str) -> dict[str, Any]:
    """Read-only DB/disk divergence report for conflict-aware UI."""
    projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    return documents_store.sync_status(document.id)


@router.post("/{project_id}/merge-disjoint")
def merge_disjoint_manuscript_changes(
    project_id: str, body: ManuscriptMergeExecute
) -> dict[str, Any]:
    if not body.confirm:
        raise ValidationError("manuscript section merge requires confirm=true")
    projects_store.require(project_id)
    document = documents_store.primary_document(project_id)
    from ...store import snapshots as snapshots_store

    safety_snapshot = snapshots_store.capture(
        project_id,
        label="before merging disjoint database and disk manuscript changes",
        kind="manual",
    )
    result = documents_store.merge_disjoint_changes(
        document.id, preview_token=body.preview_token
    )
    return {**result, "safety_snapshot": safety_snapshot}

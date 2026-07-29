"""Selected workbench layout and classified resource imports.

The desktop launcher chooses a normal folder and starts the backend with
``PAPERCREATOR_WORKBENCH``.  This API exposes the managed ``.papercreator``
layout and imports source material by *copying* it into a category.  The copy,
not the original external path, is the runtime source of truth.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ...core import events
from ...core.db import execute, query_one
from ...core.errors import ValidationError
from ...core.jobs import JobContext, manager
from ...core.models import Author, Paper, WorkbenchResourceKind
from ...core.paths import MANAGED_DIRNAME, WORKBENCH_SCHEMA_VERSION, get_paths
from ...core.util import utc_now_iso
from ...store import papers as papers_store
from ...store import projects as projects_store
from ...store import resources as resources_store

router = APIRouter(prefix="/api/workbench", tags=["workbench"])

_CATEGORY_INFO: dict[str, dict[str, str]] = {
    "idea": {
        "label": "Ideas",
        "label_zh": "研究想法",
        "description": "Unformed research ideas and contribution hypotheses.",
        "description_zh": "尚未形成论文的研究设想、问题定义和拟贡献。",
    },
    "reference_paper": {
        "label": "Reference papers",
        "label_zh": "参考论文",
        "description": "Papers by others used for retrieval, reading and analysis.",
        "description_zh": "用于检索、阅读、综述和图谱分析的他人论文。",
    },
    "own_paper": {
        "label": "My papers",
        "label_zh": "我的论文",
        "description": "Your published work, prior drafts and manuscripts.",
        "description_zh": "你已经发表的成果、旧稿和现有手稿，与参考论文分开。",
    },
    "code_project": {
        "label": "Code projects",
        "label_zh": "项目代码",
        "description": "Research implementations and Git repositories.",
        "description_zh": "实验实现和 Git 仓库；导入时排除依赖、构建产物和 .env 密钥。",
    },
    "dataset": {
        "label": "Datasets",
        "label_zh": "数据集",
        "description": "Data used or produced by experiments.",
        "description_zh": "实验使用或生成的数据，与代码和论文独立管理。",
    },
    "supplementary": {
        "label": "Supplementary",
        "label_zh": "补充材料",
        "description": "Figures, tables, protocols and other supporting files.",
        "description_zh": "图片、表格、实验协议及其他补充文件。",
    },
    "inbox": {
        "label": "Inbox",
        "label_zh": "待分类",
        "description": "Temporary landing area for material not classified yet.",
        "description_zh": "暂时无法判断类别的材料；应在整理后移入正式分类。",
    },
}


def _render(resource: Any) -> dict[str, Any]:
    payload = resource.model_dump()
    payload["path"] = str(resources_store.absolute_path(resource))
    payload["exists"] = resources_store.absolute_path(resource).exists()
    return payload


@router.get("")
def get_workbench() -> dict[str, Any]:
    """Workbench identity, physical layout and counts for the start page."""
    paths = get_paths()
    counts = resources_store.stats()
    disk = shutil.disk_usage(paths.home)
    category_paths = resources_store.category_directories()
    last_project = query_one(
        "SELECT value FROM app_state WHERE key='last_project_id'"
    )
    categories = []
    for kind in resources_store.RESOURCE_KINDS:
        categories.append(
            {
                "kind": kind,
                **_CATEGORY_INFO[kind],
                "path": category_paths[kind],
                "count": counts[kind],
            }
        )
    return {
        "product": "PaperCreator",
        "format": "papercreator-workbench",
        "schema_version": WORKBENCH_SCHEMA_VERSION,
        "workbench": str(paths.workbench_root or paths.home.parent),
        "managed_directory": str(paths.home),
        "managed_directory_name": MANAGED_DIRNAME,
        "projects_directory": str(paths.workspace),
        "project_count": len(projects_store.list_projects(with_counts=False)),
        "last_project_id": str(last_project["value"] if last_project else ""),
        "categories": categories,
        "storage": {
            "free_bytes": disk.free,
            "total_bytes": disk.total,
        },
        "rules": {
            "imports_are_copied": True,
            "external_paths_are_provenance_only": True,
            "writing_projects_are_separate": True,
        },
    }


class WorkbenchStatePatch(BaseModel):
    last_project_id: str = ""


@router.patch("/state")
def update_workbench_state(request: WorkbenchStatePatch) -> dict[str, Any]:
    """Persist UI resume state in the workbench DB, never browser localStorage."""
    if request.last_project_id:
        projects_store.require(request.last_project_id)
    execute(
        "INSERT INTO app_state (key,value,updated_at) VALUES ('last_project_id',?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (request.last_project_id, utc_now_iso()),
    )
    return {"last_project_id": request.last_project_id}


@router.get("/resources")
def list_resources(
    kind: str = "",
    project_id: str = "",
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    items = resources_store.list_resources(
        kind=kind, project_id=project_id, limit=limit
    )
    return {"items": [_render(item) for item in items], "total": len(items)}


class ResourceImportRequest(BaseModel):
    kind: WorkbenchResourceKind
    source_path: str = ""
    title: str = ""
    description: str = ""
    content: str = ""
    project_id: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    keywords: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _add_to_project(project_id: str, paper_ids: list[str], name: str) -> None:
    if not project_id or not paper_ids:
        return
    projects_store.require(project_id)
    collection = papers_store.ensure_collection(project_id, name, kind="imported")
    papers_store.add_to_collection(collection["id"], paper_ids)


def _import_external_resource(
    request: ResourceImportRequest,
    *,
    job: JobContext | None = None,
) -> dict[str, Any]:
    """Copy/register a path and perform format-specific post-processing."""
    resource = resources_store.import_path(
        request.source_path,
        kind=request.kind,
        title=request.title,
        description=request.description,
        project_id=request.project_id,
        metadata=request.metadata,
        progress=job.progress if job else None,
        checkpoint=job.raise_if_cancelled if job else None,
    )
    managed = resources_store.absolute_path(resource)
    stored_papers: list[Paper] = []
    warnings: list[str] = []

    if job:
        job.raise_if_cancelled()
        job.progress(0.98, "Processing the completed managed copy…")

    if request.kind in ("reference_paper", "own_paper") and managed.is_file():
        suffix = managed.suffix.lower()
        if suffix in (".bib", ".ris", ".csv", ".json"):
            from ...retrieval.providers.local_files import parse_file

            parsed = parse_file(managed)
            for paper in parsed:
                paper.origin = (
                    "own_paper" if request.kind == "own_paper" else "manual"
                )
                paper.raw.setdefault("workbench", {})["resource_id"] = resource.id
            stored_papers, _inserted, _updated = papers_store.upsert_many(parsed)
            if not parsed:
                warnings.append("No paper records could be parsed from the managed copy.")
        elif suffix in (".pdf", ".docx", ".md", ".markdown", ".txt", ".tex"):
            from ...importers import DocumentExtraction, extract_document

            try:
                extracted = extract_document(managed)
            except Exception as exc:  # noqa: BLE001 - user-supplied/corrupt document
                extracted = DocumentExtraction(
                    title=managed.stem,
                    method="failed",
                    warnings=[
                        f"Text extraction failed ({type(exc).__name__}); the managed file is still preserved."
                    ],
                )
            resource = resources_store.attach_extraction(
                resource.id,
                extracted.text,
                extracted.audit(),
            )
            warnings.extend(extracted.warnings)
            paper = Paper(
                title=request.title.strip() or extracted.title or managed.stem,
                # A substantial preview powers full-text search and landscape
                # placement without inflating the database with an entire book.
                abstract=extracted.abstract_text,
                authors=[
                    Author(name=name)
                    for name in (
                        [value for value in request.authors if value.strip()]
                        or extracted.authors
                    )
                ],
                year=request.year,
                venue=request.venue,
                keywords=request.keywords,
                pdf_path=str(managed) if suffix == ".pdf" else "",
                origin="own_paper" if request.kind == "own_paper" else "manual",
                source_providers=["user"],
                raw={
                    "workbench": {
                        "resource_id": resource.id,
                        "extraction": extracted.audit(),
                    }
                },
            ).ensure_id()
            stored_papers = [papers_store.upsert(paper)]
            if not extracted.text:
                warnings.append(
                    "The managed file was imported, but it produced no searchable text."
                )
        else:
            warnings.append(
                "The file was safely copied, but its format is not parsed into the paper library."
            )

    paper_ids = [paper.id for paper in stored_papers]
    if paper_ids:
        resource = resources_store.attach_papers(resource.id, paper_ids)
        collection_name = (
            "my papers" if request.kind == "own_paper" else "imported references"
        )
        _add_to_project(request.project_id, paper_ids, collection_name)

    payload = {
        "resource": _render(resource),
        "papers": [paper.model_dump() for paper in stored_papers],
        "warnings": warnings,
    }
    events.publish(
        events.LIBRARY_UPDATED,
        {
            "reason": "resource_imported",
            "resourceId": resource.id,
            "resourceKind": resource.kind,
            "paperCount": len(stored_papers),
        },
        project_id=request.project_id or None,
        job_id=job.job_id if job else None,
    )
    return payload


@router.post("/resources")
def import_resource(request: ResourceImportRequest) -> dict[str, Any]:
    """Create an idea or copy a file/folder into a classified category.

    Bibliography files are parsed after the managed copy is complete. PDFs and
    manuscript files create a minimal library record that the user can enrich
    later. Code, data and supplementary resources remain filesystem resources.
    """
    if request.project_id:
        projects_store.require(request.project_id)

    if request.kind == "idea":
        content = request.content.strip() or request.description.strip()
        title = request.title.strip() or content.splitlines()[0][:120]
        if not title and not content:
            raise ValidationError("an idea needs a title or content")
        paper = Paper(
            title=title,
            abstract=content,
            authors=[Author(name=name) for name in request.authors if name.strip()],
            year=request.year,
            venue=request.venue,
            keywords=request.keywords,
            origin="idea",
            source_providers=["user"],
            raw={"workbench_kind": "idea"},
        ).ensure_id()
        stored = papers_store.upsert(paper)
        resource = resources_store.create_idea(
            title=title,
            content=content,
            project_id=request.project_id,
            paper_id=stored.id,
            metadata=request.metadata,
        )
        _add_to_project(request.project_id, [stored.id], "my ideas")
        return {"resource": _render(resource), "papers": [stored.model_dump()]}

    if (
        request.kind in ("reference_paper", "own_paper")
        and not request.source_path
    ):
        content = request.content.strip() or request.description.strip()
        if not request.title.strip() and not content:
            raise ValidationError("a metadata-only paper needs a title or abstract")
        paper = Paper(
            title=request.title.strip() or content[:120],
            abstract=content,
            authors=[Author(name=name) for name in request.authors if name.strip()],
            year=request.year,
            venue=request.venue,
            keywords=request.keywords,
            origin="own_paper" if request.kind == "own_paper" else "manual",
            source_providers=["user"],
            raw={"workbench_kind": request.kind},
        ).ensure_id()
        stored = papers_store.upsert(paper)
        resource = resources_store.create_text(
            kind=request.kind,
            title=paper.title,
            content=content,
            project_id=request.project_id,
            paper_id=stored.id,
            metadata=request.metadata,
        )
        _add_to_project(
            request.project_id,
            [stored.id],
            "my papers" if request.kind == "own_paper" else "imported references",
        )
        return {"resource": _render(resource), "papers": [stored.model_dump()]}

    if not request.source_path:
        raise ValidationError("source_path is required for this resource kind")
    return _import_external_resource(request)


@router.post("/resources/import", status_code=202)
def start_directory_import(request: ResourceImportRequest) -> dict[str, Any]:
    """Queue a cancellable, atomic directory import.

    File and metadata imports keep the synchronous endpoint for backwards
    compatibility.  Directories can be arbitrarily large and must use this
    Job/SSE path so an HTTP request never hides minutes of copying.
    """
    if request.kind in ("idea", "reference_paper", "own_paper"):
        raise ValidationError(
            "background directory import is for code, data, supplementary or inbox resources"
        )
    if not request.source_path:
        raise ValidationError("source_path is required for a directory import")
    source = Path(request.source_path).expanduser()
    if not source.exists() or not source.is_dir():
        raise ValidationError("the background import source must be a directory")
    if request.project_id:
        projects_store.require(request.project_id)

    request_payload = request.model_dump()

    def worker(ctx: JobContext) -> dict[str, Any]:
        return _import_external_resource(
            ResourceImportRequest.model_validate(request_payload), job=ctx
        )

    handle = manager.submit(
        "resource_import",
        worker,
        payload={
            "kind": request.kind,
            "source_path": str(source),
            "title": request.title,
            "project_id": request.project_id,
            "atomic": True,
            "link_policy": "never_follow",
        },
        project_id=request.project_id or None,
    )
    return {
        "job_id": handle.id,
        "status": "queued",
        "kind": "resource_import",
        "source_path": str(source),
    }


@router.delete("/resources/{resource_id}")
def delete_resource(resource_id: str, remove_files: bool = False) -> dict[str, Any]:
    """Forget a resource; managed files are removed only with explicit opt-in."""
    return resources_store.delete(resource_id, remove_files=remove_files)

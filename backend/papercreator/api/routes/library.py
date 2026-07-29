"""Library routes: browse, edit, tag, import, add own ideas, find duplicates.

The library is global (shared by every project), so deletes here affect all
projects. Project membership is managed through the project routes' collection
endpoints.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ...core.errors import NotFoundError, ValidationError
from ...core.logging_setup import get_logger
from ...core.models import Author, Paper
from ...store import papers as papers_store

log = get_logger(__name__)
router = APIRouter(prefix="/api/library", tags=["library"])


@router.get("")
def browse(
    text: str = "",
    project_id: str = "",
    collection_id: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    origin: str = "",
    read_status: str = "",
    tag: str = "",
    min_rating: int = 0,
    open_access_only: bool = False,
    sort: str = "updated",
    limit: int = Query(100, le=500),
    offset: int = 0,
) -> dict[str, Any]:
    """Filtered paper list. ``text`` runs full-text search over title/abstract."""
    return papers_store.search_library(
        text=text, project_id=project_id, collection_id=collection_id,
        year_from=year_from, year_to=year_to, origin=origin,
        read_status=read_status, tag=tag, min_rating=min_rating,
        open_access_only=open_access_only, sort=sort, limit=limit, offset=offset,
    )


@router.get("/stats")
def stats() -> dict[str, Any]:
    return {"library": papers_store.library_stats(), "tags": papers_store.all_tags()}


@router.get("/{paper_id}")
def get_paper(paper_id: str) -> dict[str, Any]:
    paper = papers_store.get(paper_id)
    if paper is None:
        raise NotFoundError(f"paper {paper_id} not found")
    return paper.model_dump()


class PaperPatch(BaseModel):
    title: str | None = None
    abstract: str | None = None
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    url: str | None = None
    notes: str | None = None
    rating: int | None = None
    read_status: str | None = None
    tags: list[str] | None = None
    keywords: list[str] | None = None


@router.patch("/{paper_id}")
def update_paper(paper_id: str, request: PaperPatch) -> dict[str, Any]:
    """Edit a paper. User-owned fields (notes, rating, tags) survive re-imports."""
    fields = {k: v for k, v in request.model_dump().items() if v is not None}
    if not fields:
        raise ValidationError("no fields to update")
    return papers_store.update_fields(paper_id, **fields).model_dump()


@router.delete("/{paper_id}")
def delete_paper(paper_id: str) -> dict[str, Any]:
    """Remove a paper from the library entirely, for every project."""
    if not papers_store.get(paper_id):
        raise NotFoundError(f"paper {paper_id} not found")
    return {
        "deleted": papers_store.delete(paper_id),
        "note": "removed from the global library and from every project's "
                "collections; saved analyses keep the point but mark it as removed",
    }


class BulkDelete(BaseModel):
    paper_ids: list[str]


@router.post("/delete")
def delete_papers(request: BulkDelete) -> dict[str, Any]:
    return {
        "deleted": papers_store.delete_many(request.paper_ids),
        "requested": len(request.paper_ids),
    }


class BulkTag(BaseModel):
    paper_ids: list[str]
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


@router.post("/tag")
def bulk_tag(request: BulkTag) -> dict[str, Any]:
    """Add or remove tags across many papers."""
    updated = 0
    for paper_id in request.paper_ids:
        paper = papers_store.get(paper_id)
        if paper is None:
            continue
        tags = [t for t in paper.tags if t not in request.remove]
        for tag in request.add:
            if tag not in tags:
                tags.append(tag)
        papers_store.update_fields(paper_id, tags=tags)
        updated += 1
    return {"updated": updated}


class ManualPaper(BaseModel):
    """A paper entered by hand, or the user's own idea/paper."""

    title: str
    abstract: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    doi: str = ""
    url: str = ""
    keywords: list[str] = Field(default_factory=list)
    # retrieved | manual | idea | own_paper
    origin: str = "manual"
    notes: str = ""
    project_id: str = ""
    collection_name: str = ""


@router.post("/papers")
def add_paper(request: ManualPaper) -> dict[str, Any]:
    """Add a paper manually, or register the user's own idea or paper.

    ``origin='idea'`` / ``'own_paper'`` is what makes a record eligible to be
    marked as a seed point in the landscape, which is the "add my own idea to the
    map" requirement.
    """
    if not request.title.strip() and not request.abstract.strip():
        raise ValidationError("a title or an abstract is required")
    if request.origin not in ("retrieved", "manual", "idea", "own_paper"):
        raise ValidationError(
            "origin must be one of: retrieved, manual, idea, own_paper"
        )
    paper = Paper(
        title=request.title.strip() or request.abstract.strip()[:120],
        abstract=request.abstract,
        authors=[Author(name=n) for n in request.authors if n.strip()],
        year=request.year,
        venue=request.venue,
        doi=request.doi,
        url=request.url,
        keywords=request.keywords,
        origin=request.origin,  # type: ignore[arg-type]
        notes=request.notes,
        source_providers=["user"],
    ).ensure_id()
    stored = papers_store.upsert(paper)
    result: dict[str, Any] = {"paper": stored.model_dump()}
    if request.project_id:
        collection = papers_store.ensure_collection(
            request.project_id,
            request.collection_name or papers_store.DEFAULT_COLLECTION,
        )
        papers_store.add_to_collection(collection["id"], [stored.id])
        result["added_to_project"] = request.project_id
    return result


class ImportFileRequest(BaseModel):
    path: str
    project_id: str = ""
    collection_name: str = ""


@router.post("/import")
def import_file(request: ImportFileRequest) -> dict[str, Any]:
    """Import bibliography through a managed workbench copy.

    Kept for API compatibility; the classified workbench endpoint is the richer
    UI surface. Runtime parsing never depends on the original external file.
    """
    from ...retrieval.providers import local_files
    from ...store import resources as resources_store

    path = Path(request.path).expanduser()
    if not path.is_file():
        raise ValidationError(f"'{path}' is not a file")
    if path.suffix.lower() not in (".bib", ".ris", ".csv", ".json"):
        raise ValidationError(
            f"unsupported format '{path.suffix}'. Supported: .bib, .ris, .csv, .json"
        )
    resource = resources_store.import_path(
        str(path),
        kind="reference_paper",
        project_id=request.project_id,
    )
    managed = resources_store.absolute_path(resource)
    parsed = local_files.parse_file(managed)
    if not parsed:
        raise ValidationError(
            f"no records could be parsed from '{path.name}'. Check the file "
            f"format."
        )
    stored, inserted, updated = papers_store.upsert_many(parsed)
    resource = resources_store.attach_papers(resource.id, [paper.id for paper in stored])
    result: dict[str, Any] = {
        "file": str(path),
        "managed_file": str(managed),
        "resource_id": resource.id,
        "parsed": len(parsed),
        "inserted": inserted,
        "updated": updated,
    }
    if request.project_id:
        collection = papers_store.ensure_collection(
            request.project_id,
            request.collection_name or "imported",
            kind="imported",
        )
        papers_store.add_to_collection(collection["id"], [p.id for p in stored])
        result["added_to_project"] = request.project_id
    log.info("imported %s records from %s", len(parsed), path)
    return result


@router.get("/duplicates")
def find_duplicates(
    project_id: str = "", threshold: float = Query(0.92, ge=0.5, le=1.0)
) -> dict[str, Any]:
    """Report suspected duplicate groups without merging anything.

    Merging is left to the user: an automatic merge that is wrong silently loses a
    distinct paper, which is worse for a literature review than a visible
    duplicate.
    """
    from ...retrieval import dedupe

    if project_id:
        paper_ids = papers_store.project_paper_ids(project_id)
        papers = papers_store.get_many(paper_ids)
    else:
        listing = papers_store.search_library(limit=5000)
        papers = [Paper.model_validate(item) for item in listing["items"]]

    groups = dedupe.find_duplicates_in_library(papers, title_threshold=threshold)
    by_id = {p.id: p for p in papers}
    return {
        "scanned": len(papers),
        "threshold": threshold,
        "groups": [
            {
                "paper_ids": group,
                "papers": [
                    {
                        "id": pid,
                        "title": by_id[pid].title if pid in by_id else "",
                        "year": by_id[pid].year if pid in by_id else None,
                        "venue": by_id[pid].venue if pid in by_id else "",
                        "providers": by_id[pid].source_providers if pid in by_id else [],
                        "citations": by_id[pid].citation_count if pid in by_id else 0,
                    }
                    for pid in group
                ],
            }
            for group in groups
        ],
    }


class MergeRequest(BaseModel):
    keep_id: str
    merge_ids: list[str]


@router.post("/merge")
def merge_papers(request: MergeRequest) -> dict[str, Any]:
    """Merge duplicates into one record, then delete the others.

    Collection membership from the merged records is transferred to the survivor
    first, so no project loses a paper.
    """
    keeper = papers_store.get(request.keep_id)
    if keeper is None:
        raise NotFoundError(f"paper {request.keep_id} not found")

    from ...core.db import query
    from ...store.papers import merge_papers as merge_fn

    merged_collections = 0
    for paper_id in request.merge_ids:
        if paper_id == request.keep_id:
            continue
        other = papers_store.get(paper_id)
        if other is None:
            continue
        keeper = merge_fn(keeper, other)
        for row in query(
            "SELECT collection_id FROM collection_items WHERE paper_id=?", (paper_id,)
        ):
            papers_store.add_to_collection(row["collection_id"], [request.keep_id])
            merged_collections += 1
    keeper.id = request.keep_id
    stored = papers_store.upsert(keeper)
    removed = papers_store.delete_many(
        [pid for pid in request.merge_ids if pid != request.keep_id]
    )
    return {
        "paper": stored.model_dump(),
        "removed": removed,
        "collection_links_transferred": merged_collections,
    }


class DownloadRequest(BaseModel):
    paper_ids: list[str]


@router.post("/download-pdfs")
def download_pdfs(request: DownloadRequest) -> dict[str, Any]:
    """Download open-access PDFs for the given papers, as a background job.

    Only papers with a ``pdf_url`` are attempted, and only open-access ones -
    this never circumvents a paywall.
    """
    from ...core.jobs import JobContext, manager

    def work(ctx: JobContext) -> dict[str, Any]:
        import asyncio

        from ...core.paths import get_paths
        from ...retrieval.http_client import HttpClient

        async def run() -> dict[str, Any]:
            downloaded: list[str] = []
            skipped: list[dict[str, str]] = []
            async with HttpClient() as client:
                for index, paper_id in enumerate(request.paper_ids):
                    ctx.raise_if_cancelled()
                    ctx.progress(
                        index / max(1, len(request.paper_ids)),
                        f"downloading {index + 1}/{len(request.paper_ids)}",
                    )
                    paper = papers_store.get(paper_id)
                    if paper is None:
                        skipped.append({"id": paper_id, "reason": "not found"})
                        continue
                    if not paper.pdf_url:
                        skipped.append({"id": paper_id, "reason": "no PDF url"})
                        continue
                    if not paper.is_open_access:
                        skipped.append({
                            "id": paper_id,
                            "reason": "not marked open access; not downloaded",
                        })
                        continue
                    target = get_paths().pdf_dir / f"{paper.id}.pdf"
                    try:
                        await client.download(paper.pdf_url, target)
                    except Exception as exc:  # noqa: BLE001 - per-paper failure
                        skipped.append({"id": paper_id, "reason": str(exc)[:120]})
                        continue
                    papers_store.update_fields(paper_id, pdf_path=str(target))
                    downloaded.append(paper_id)
            return {"downloaded": downloaded, "skipped": skipped}

        return asyncio.run(run())

    handle = manager.submit(
        "download_pdfs", work, payload={"count": len(request.paper_ids)}
    )
    return {"job_id": handle.id, "requested": len(request.paper_ids)}

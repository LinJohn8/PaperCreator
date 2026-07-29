"""Search routes: keyword, idea-based, paper-based, and identifier lookup.

Two execution modes for every search:

* ``POST /api/search`` runs it as a background job and returns a job id. This is
  the normal path - a multi-provider search takes 2-20 seconds and progress
  streams over SSE.
* ``POST /api/search/sync`` blocks and returns the results. For scripts, tests,
  and small single-provider queries where a round trip is simpler.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ...core.errors import NotFoundError, ValidationError
from ...core.logging_setup import get_logger
from ...core.models import SearchRequest
from ...store import papers as papers_store

log = get_logger(__name__)
router = APIRouter(prefix="/api/search", tags=["search"])


class SearchBody(BaseModel):
    """Search input. Mirrors :class:`~papercreator.core.models.SearchRequest`."""

    query: str = ""
    mode: str = "keyword"
    seed_text: str = ""
    providers: list[str] = Field(default_factory=list)
    limit_per_provider: int = 50
    total_limit: int = 300
    year_from: int | None = None
    year_to: int | None = None
    open_access_only: bool = False
    venues: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    fields_of_study: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    sort: str = "relevance"
    project_id: str = ""
    collection_name: str = ""
    use_cache: bool = True
    # Expand an idea/abstract into search queries with the LLM. Falls back to
    # rule-based expansion when no model is configured.
    use_llm_expansion: bool = True

    def to_request(self) -> SearchRequest:
        return SearchRequest(**self.model_dump())


def _validate(body: SearchBody) -> None:
    if body.mode in ("idea", "paper"):
        if not body.seed_text.strip() and not body.query.strip():
            raise ValidationError(
                f"{body.mode} mode needs seed_text - the idea or abstract to find "
                f"related work for"
            )
    elif not body.query.strip():
        raise ValidationError("a keyword search needs a query")


@router.get("/providers")
def providers() -> dict[str, Any]:
    """Provider catalogue with availability and capabilities."""
    from ...retrieval import registry

    return {"providers": registry.describe_all()}


@router.post("")
def submit_search(body: SearchBody) -> dict[str, Any]:
    """Queue a search. Follow progress on ``/api/system/events``."""
    _validate(body)
    from ...retrieval import pipeline

    request = body.to_request()
    job_id = pipeline.submit_search(request)
    return {
        "job_id": job_id,
        "mode": request.mode,
        "providers": request.providers or "(enabled defaults)",
        "note": "subscribe to /api/system/events for per-provider progress",
    }


@router.post("/sync")
def search_now(body: SearchBody, persist: bool = Query(True)) -> dict[str, Any]:
    """Run a search and return the results directly."""
    _validate(body)
    from ...retrieval import pipeline

    response = pipeline.search_sync(
        body.to_request(), persist=persist,
    )
    return response.model_dump()


class ExpandBody(BaseModel):
    query: str = ""
    seed_text: str = ""
    use_llm: bool = True
    max_queries: int = 6


@router.post("/expand")
async def expand_query(body: ExpandBody) -> dict[str, Any]:
    """Preview how an idea becomes search queries.

    Exposed separately so the user can inspect and edit the expansion before
    spending provider quota on it.
    """
    from ...retrieval import query_expand

    if not body.query.strip() and not body.seed_text.strip():
        raise ValidationError("provide a query or seed_text to expand")
    return await query_expand.expand(
        query=body.query, seed_text=body.seed_text,
        use_llm=body.use_llm, max_queries=body.max_queries,
    )


class ResolveBody(BaseModel):
    identifier: str
    providers: list[str] = Field(default_factory=list)
    add_to_project: str = ""


@router.post("/resolve")
async def resolve_identifier(body: ResolveBody) -> dict[str, Any]:
    """Look up one DOI / arXiv id / PMID across providers and merge the results."""
    from ...retrieval import pipeline

    identifier = body.identifier.strip()
    if not identifier:
        raise ValidationError("provide a DOI, arXiv id or PMID")
    paper = await pipeline.resolve_identifier(
        identifier, providers=body.providers or None
    )
    if paper is None:
        raise NotFoundError(
            f"no provider could resolve '{identifier}'. Check the identifier, or "
            f"add the paper manually."
        )
    stored = papers_store.upsert(paper)
    result: dict[str, Any] = {"paper": stored.model_dump()}
    if body.add_to_project:
        collection = papers_store.ensure_collection(body.add_to_project)
        papers_store.add_to_collection(collection["id"], [stored.id])
        result["added_to_project"] = body.add_to_project
    return result


@router.get("/history")
def search_history(
    project_id: str = "", limit: int = Query(50, le=200)
) -> dict[str, Any]:
    return {"items": papers_store.list_searches(project_id=project_id, limit=limit)}


@router.get("/history/{search_id}")
def get_search(search_id: str) -> dict[str, Any]:
    """A past search with its result set, so it can be reviewed or re-run."""
    record = papers_store.get_search(search_id)
    if record is None:
        raise NotFoundError(f"search {search_id} not found")
    paper_ids = [r["paper_id"] for r in record.get("results", [])]
    papers = papers_store.get_many(paper_ids)
    record["papers"] = [p.model_dump() for p in papers]
    return record


@router.delete("/history/{search_id}")
def delete_search(search_id: str) -> dict[str, Any]:
    return {"deleted": papers_store.delete_search(search_id)}


@router.post("/history/{search_id}/rerun")
def rerun_search(search_id: str, use_cache: bool = Query(False)) -> dict[str, Any]:
    """Re-run a stored search with the same parameters.

    Cache is bypassed by default: the point of re-running is usually to pick up
    newly published work.
    """
    from ...retrieval import pipeline

    record = papers_store.get_search(search_id)
    if record is None:
        raise NotFoundError(f"search {search_id} not found")
    params = record.get("params") or {}
    params["use_cache"] = use_cache
    request = SearchRequest.model_validate(params)
    return {"job_id": pipeline.submit_search(request), "original": search_id}

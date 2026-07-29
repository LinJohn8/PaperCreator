"""Search pipeline: the orchestration that turns a request into stored papers.

Flow::

    SearchRequest
      -> expand queries (rules, optionally LLM)          query_expand
      -> select providers (enabled + available)          registry
      -> fan out concurrently, per-provider rate limited providers/*
      -> post-filter what providers could not filter     rank.apply_post_filters
      -> deduplicate and merge across providers          dedupe
      -> rank by reciprocal rank fusion + signals        rank
      -> persist to library, link to collection          store.papers
      -> record the search for history/reproducibility   store.papers

Design points:

* **Providers run concurrently** with ``asyncio.gather``, but each is throttled
  by its own limiter, so a slow source (arXiv at 1 req/3s) never blocks a fast
  one and no source's terms of use are exceeded.
* **Partial success is success.** A provider that fails contributes a stats row
  with an error and the search still returns. Only *zero* providers producing
  anything is treated as a failed search, and even then results already stored
  are kept.
* **Progress is streamed.** Each provider's completion emits an SSE event, so the
  UI fills in as sources report rather than waiting for the slowest.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..core import events
from ..core.config import get_settings
from ..core.jobs import JobContext
from ..core.logging_setup import get_logger
from ..core.models import Paper, ProviderStats, SearchRequest, SearchResponse
from ..store import papers as papers_store
from . import dedupe as dedupe_module
from . import query_expand, rank
from .http_client import HttpClient
from .registry import resolve_selection

log = get_logger(__name__)


async def _run_provider(
    provider: Any,
    request: SearchRequest,
    limit: int,
    *,
    job: JobContext | None,
    total_providers: int,
    completed: list[int],
) -> tuple[str, list[Paper], ProviderStats]:
    """Run one provider and emit progress. Never raises."""
    papers, stats = await provider.safe_search(request, limit)
    completed[0] += 1
    fraction = 0.15 + 0.55 * (completed[0] / max(1, total_providers))
    message = (
        f"{provider.meta.name}: {stats.count} results"
        if not stats.error else f"{provider.meta.name}: {stats.error}"
    )
    if job is not None:
        job.progress(fraction, message)
    events.publish(
        events.SEARCH_PROVIDER,
        {
            "provider": provider.id,
            "providerName": provider.meta.name,
            "count": stats.count,
            "durationMs": stats.duration_ms,
            "outcome": stats.outcome,
            "error": stats.error,
            "errorCode": stats.error_code,
            "retryable": stats.retryable,
            "httpStatus": stats.http_status,
            "retryAfterS": stats.retry_after_s,
            "hint": stats.hint,
            "completed": completed[0],
            "total": total_providers,
        },
        project_id=request.project_id or None,
        job_id=job.job_id if job else None,
    )
    return provider.id, papers, stats


async def search_async(
    request: SearchRequest,
    *,
    job: JobContext | None = None,
    persist: bool = True,
    use_llm_expansion: bool | None = None,
) -> SearchResponse:
    """Execute a search end to end.

    ``persist=False`` runs the pipeline without touching the library, which the
    agent "explore" step uses to preview candidates before committing them.
    """
    settings = get_settings()
    expansion_enabled = (
        request.use_llm_expansion
        if use_llm_expansion is None
        else use_llm_expansion
    )
    started = time.perf_counter()
    warnings: list[str] = []

    # ---------------------------------------------------- 1. expand queries
    if job is not None:
        job.progress(0.05, "preparing queries")
    if request.mode in ("idea", "paper") and (request.seed_text or request.query):
        expansion = await query_expand.expand(
            query=request.query,
            seed_text=request.seed_text,
            use_llm=expansion_enabled,
        )
        # Keep the user's literal query as the first variant.
        request.expanded_queries = [
            q for q in expansion.get("queries", []) if q != request.query
        ]
        if expansion.get("notes"):
            warnings.append(str(expansion["notes"]))
        log.info(
            "expanded %s-mode search into %s queries via %s",
            request.mode, len(request.expanded_queries) + 1,
            expansion.get("method"),
        )
    elif request.mode == "keyword" and not request.query.strip():
        warnings.append("empty query")

    # -------------------------------------------------- 2. select providers
    async with HttpClient(use_cache=request.use_cache) as client:
        providers, selection_warnings = resolve_selection(request.providers, client)
        warnings.extend(selection_warnings)
        if not providers:
            search_id = ""
            if persist:
                search_id = papers_store.record_search(
                    query_text=request.query,
                    mode=request.mode,
                    seed_text=request.seed_text,
                    providers=request.providers,
                    params=request.model_dump(),
                    papers=[],
                    provider_stats={},
                    project_id=request.project_id,
                )
            response = SearchResponse(
                search_id=search_id,
                query=request.query, mode=request.mode,
                warnings=[*warnings, "no retrieval provider available"],
                request=request.model_dump(),
            )
            if job is not None:
                job.progress(1.0, "no retrieval provider available")
            events.publish(
                events.SEARCH_DONE,
                {
                    "searchId": search_id,
                    "count": 0,
                    "beforeDedupe": 0,
                    "merged": 0,
                    "failedProviders": 0,
                    "unavailableProviders": len(request.providers),
                    "durationMs": int((time.perf_counter() - started) * 1000),
                },
                project_id=request.project_id or None,
                job_id=job.job_id if job else None,
            )
            return response

        per_provider = max(
            1,
            request.limit_per_provider or settings.retrieval.default_limit_per_provider,
        )
        if job is not None:
            job.progress(
                0.15,
                f"querying {len(providers)} source(s): "
                f"{', '.join(p.meta.name for p in providers)}",
            )

        # ----------------------------------------------------- 3. fan out
        completed = [0]
        results = await asyncio.gather(*[
            _run_provider(
                provider, request, per_provider,
                job=job, total_providers=len(providers), completed=completed,
            )
            for provider in providers
        ])

    # Ordered lists per provider feed reciprocal rank fusion; capture before
    # dedupe merges ids away.
    provider_lists: dict[str, list[str]] = {}
    all_papers: list[Paper] = []
    stats: list[ProviderStats] = []
    for provider_id, papers, provider_stats in results:
        stats.append(provider_stats)
        provider_lists[provider_id] = [p.ensure_id().id for p in papers]
        all_papers.extend(papers)

    total_before = len(all_papers)
    failed_stats = [stat for stat in stats if stat.outcome != "success"]
    if failed_stats and all_papers:
        outcomes = ", ".join(
            f"{stat.provider}: {stat.outcome}" for stat in failed_stats
        )
        warnings.append(
            f"partial provider failure ({len(failed_stats)}/{len(stats)}): {outcomes}"
        )
    if not all_papers:
        if failed_stats:
            outcomes = ", ".join(
                f"{stat.provider}: {stat.outcome}" for stat in failed_stats
            )
            warnings.append(
                f"all selected providers failed ({outcomes})"
                if len(failed_stats) == len(stats)
                else f"provider failures produced no results ({outcomes})"
            )
        elif not warnings:
            warnings.append("no results")
        search_id = ""
        if persist:
            search_id = papers_store.record_search(
                query_text=request.query,
                mode=request.mode,
                seed_text=request.seed_text,
                providers=[stat.provider for stat in stats],
                params=request.model_dump(),
                papers=[],
                provider_stats={
                    stat.provider: stat.model_dump(exclude={"provider"})
                    for stat in stats
                },
                project_id=request.project_id,
            )
        response = SearchResponse(
            search_id=search_id,
            query=request.query,
            mode=request.mode,
            stats=stats,
            warnings=warnings,
            request=request.model_dump(),
        )
        if job is not None:
            job.progress(1.0, "no papers found; provider diagnostics saved")
        events.publish(
            events.SEARCH_DONE,
            {
                "searchId": search_id,
                "count": 0,
                "beforeDedupe": 0,
                "merged": 0,
                "failedProviders": len(failed_stats),
                "durationMs": int((time.perf_counter() - started) * 1000),
            },
            project_id=request.project_id or None,
            job_id=job.job_id if job else None,
        )
        return response

    # ------------------------------------------------- 4. filter + dedupe
    if job is not None:
        job.progress(0.72, f"merging {total_before} records")
    filtered, removed = rank.apply_post_filters(all_papers, request)
    if removed:
        warnings.append(
            "post-filtered: "
            + ", ".join(f"{count} by {reason}" for reason, count in removed.items())
        )
    unique, merged_count, dedupe_report = dedupe_module.deduplicate(
        filtered, title_threshold=settings.retrieval.dedupe_title_threshold
    )

    # -------------------------------------------------------- 5. rank
    if job is not None:
        job.progress(0.80, f"ranking {len(unique)} unique papers")
    ranked = rank.rank_papers(unique, provider_lists=provider_lists, request=request)
    total_limit = request.total_limit or settings.retrieval.total_limit
    ranked = ranked[:total_limit]

    # ------------------------------------------------------- 6. persist
    search_id = ""
    if persist:
        if job is not None:
            job.progress(0.88, f"saving {len(ranked)} papers to the library")
        stored, inserted, updated = papers_store.upsert_many(ranked)
        # upsert_many can return an existing row whose id differs from the
        # freshly computed one; use the stored ids from here on.
        ranked = stored
        if request.project_id:
            collection_name = request.collection_name or papers_store.DEFAULT_COLLECTION
            collection = papers_store.ensure_collection(
                request.project_id, collection_name,
                kind="search" if request.collection_name else "manual",
            )
            papers_store.add_to_collection(
                collection["id"],
                [p.id for p in ranked],
                scores={p.id: p.score for p in ranked},
            )
        search_id = papers_store.record_search(
            query_text=request.query,
            mode=request.mode,
            seed_text=request.seed_text,
            providers=[p.provider for p in stats],
            params=request.model_dump(),
            papers=ranked,
            provider_stats={
                s.provider: s.model_dump(exclude={"provider"}) for s in stats
            },
            project_id=request.project_id,
        )
        log.info(
            "search '%s' stored %s papers (%s new, %s updated) in %.1fs",
            (request.query or request.mode)[:60], len(ranked), inserted, updated,
            time.perf_counter() - started,
        )

    response = SearchResponse(
        search_id=search_id,
        query=request.query,
        mode=request.mode,
        papers=ranked,
        stats=stats,
        total_before_dedupe=total_before,
        total_after_dedupe=len(unique),
        duplicates_merged=merged_count,
        warnings=warnings,
        request=request.model_dump(),
    )
    if job is not None:
        job.progress(1.0, f"found {len(ranked)} papers")
    events.publish(
        events.SEARCH_DONE,
        {
            "searchId": search_id,
            "count": len(ranked),
            "beforeDedupe": total_before,
            "merged": merged_count,
            "durationMs": int((time.perf_counter() - started) * 1000),
            "dedupe": dedupe_report,
        },
        project_id=request.project_id or None,
        job_id=job.job_id if job else None,
    )
    return response


def search_sync(
    request: SearchRequest,
    *,
    job: JobContext | None = None,
    persist: bool = True,
    use_llm_expansion: bool | None = None,
) -> SearchResponse:
    """Blocking wrapper for the job runner's worker threads.

    Each job thread gets its own event loop: the providers are async, the job
    runner is threads, and creating a loop per job avoids sharing one across
    threads (which asyncio does not support).
    """
    return asyncio.run(
        search_async(
            request, job=job, persist=persist, use_llm_expansion=use_llm_expansion
        )
    )


def submit_search(request: SearchRequest) -> str:
    """Queue a search as a background job. Returns the job id."""
    from ..core.jobs import manager

    handle = manager.submit(
        "search",
        lambda ctx: search_sync(request, job=ctx).model_dump(),
        payload={"query": request.query, "mode": request.mode,
                 "providers": request.providers},
        project_id=request.project_id or None,
    )
    return handle.id


async def resolve_identifier(identifier: str, *, providers: list[str] | None = None) -> Paper | None:
    """Look up a single DOI / arXiv id / PMID across providers.

    Used by "add paper by identifier" and by the citation resolver. Tries
    providers in order of how authoritative they are for the identifier type and
    merges everything that answers, so one lookup yields the richest record.
    """
    from ..store.papers import merge_papers

    order = providers or ["openalex", "crossref", "semanticscholar", "arxiv",
                          "europepmc", "pubmed"]
    found: Paper | None = None
    async with HttpClient() as client:
        for provider_id in order:
            from .registry import build

            try:
                provider = build(provider_id, client)
            except Exception:  # noqa: BLE001 - unknown id in a custom list
                continue
            if not provider.availability().available:
                continue
            try:
                paper = await provider.fetch_by_id(identifier)
            except Exception as exc:  # noqa: BLE001 - try the next provider
                log.debug("%s could not resolve %s: %s", provider_id, identifier, exc)
                continue
            if paper is None:
                continue
            paper.ensure_id()
            if provider_id not in paper.source_providers:
                paper.source_providers.append(provider_id)
            found = paper if found is None else merge_papers(found, paper)
            # Two independent confirmations are enough; stop paying for more.
            if len(found.source_providers) >= 2 and found.abstract:
                break
    return found

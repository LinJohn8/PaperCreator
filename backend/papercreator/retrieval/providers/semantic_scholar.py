"""Semantic Scholar provider (S2 Graph API).

API: ``https://api.semanticscholar.org/graph/v1``
Docs: https://api.semanticscholar.org/api-docs/graph

Semantic Scholar is the single most useful source for this application because
it offers ``/paper/search/match`` and recommendation endpoints backed by
SPECTER embeddings - i.e. genuine *semantic* retrieval from an idea or an
existing paper, which is exactly requirement "give me papers related to my
idea/paper" without needing local embeddings first.

The catch, verified live: **keyless requests are throttled hard** (HTTP 429
immediately under any load). The provider is therefore registered as
``tier="freemium"`` and reports itself degraded without a key; it stays usable
for occasional single queries, and a free key makes it reliable.
"""

from __future__ import annotations

from typing import Any

from ...core.logging_setup import get_logger
from ...core.models import Author, Paper, SearchRequest
from ...core.util import collapse_ws, normalize_arxiv_id, normalize_doi
from ..base import (
    Provider,
    ProviderAvailability,
    ProviderCapabilities,
    ProviderMeta,
    RateLimit,
)

log = get_logger(__name__)

_BASE = "https://api.semanticscholar.org/graph/v1"
_FIELDS = ",".join([
    "paperId", "externalIds", "title", "abstract", "year", "venue",
    "publicationVenue", "publicationTypes", "citationCount", "referenceCount",
    "isOpenAccess", "openAccessPdf", "fieldsOfStudy", "s2FieldsOfStudy",
    "authors.name", "authors.authorId", "url", "publicationDate",
])

_TYPE_MAP = {
    "JournalArticle": "journal", "Conference": "conference", "Review": "review",
    "Book": "book", "BookSection": "book", "Dataset": "dataset",
    "Editorial": "editorial", "Preprint": "preprint",
}


def _parse_paper(data: dict[str, Any]) -> Paper | None:
    title = collapse_ws(data.get("title") or "")
    if not title:
        return None
    external = data.get("externalIds") or {}
    open_access_pdf = data.get("openAccessPdf") or {}
    venue_info = data.get("publicationVenue") or {}
    types = data.get("publicationTypes") or []

    fields = [f for f in (data.get("fieldsOfStudy") or []) if f]
    for entry in data.get("s2FieldsOfStudy") or []:
        category = entry.get("category")
        if category and category not in fields:
            fields.append(category)

    return Paper(
        title=title,
        abstract=collapse_ws(data.get("abstract") or ""),
        authors=[
            Author(name=collapse_ws(a.get("name") or ""))
            for a in (data.get("authors") or []) if a.get("name")
        ],
        year=data.get("year"),
        venue=collapse_ws(data.get("venue") or venue_info.get("name") or ""),
        venue_type=next(
            (_TYPE_MAP[t] for t in types if t in _TYPE_MAP),
            "journal" if data.get("venue") else "",
        ),
        doi=normalize_doi(external.get("DOI")),
        arxiv_id=normalize_arxiv_id(external.get("ArXiv")),
        pmid=str(external.get("PubMed") or ""),
        s2_id=str(data.get("paperId") or ""),
        url=data.get("url") or "",
        pdf_url=open_access_pdf.get("url") or "",
        is_open_access=bool(data.get("isOpenAccess")),
        citation_count=int(data.get("citationCount") or 0),
        reference_count=int(data.get("referenceCount") or 0),
        fields_of_study=fields[:8],
        raw={"s2": {"types": types, "publicationDate": data.get("publicationDate")}},
    )


class SemanticScholarProvider(Provider):
    meta = ProviderMeta(
        id="semanticscholar",
        name="Semantic Scholar",
        name_zh="Semantic Scholar",
        description="Semantic (SPECTER-embedding) search and recommendations. "
                    "The best free source for idea/paper similarity. Keyless "
                    "access is heavily throttled.",
        description_zh="基于 SPECTER 向量的语义检索与推荐，最适合按 idea/论文找相关工作；"
                       "无 API key 时限流严重。",
        homepage="https://www.semanticscholar.org",
        docs_url="https://api.semanticscholar.org/api-docs/graph",
        tier="freemium",
        coverage="~220M papers",
        disciplines=["all", "computer science"],
        requires_key=False,
        key_setting="semanticscholar",
        signup_url="https://www.semanticscholar.org/product/api#api-key-form",
    )
    capabilities = ProviderCapabilities(
        full_text_search=True,
        field_search=False,
        year_range=True,
        open_access_filter=True,
        venue_filter=True,
        fields_of_study_filter=True,
        sort_by_date=False,
        sort_by_citations=False,
        returns_abstract=True,
        returns_citations=True,
        returns_pdf_url=True,
        max_results_per_request=100,
        supports_pagination=True,
        semantic_query=True,
    )
    rate_limit = RateLimit(
        min_interval_s=1.1, max_concurrency=1, max_retries=1, max_queries=2,
        note="keyless access returns HTTP 429 under load; a free key lifts this",
    )

    def availability(self) -> ProviderAvailability:
        """Usable without a key, but say so - the UI shows a warning badge."""
        if not self.api_key():
            return ProviderAvailability(
                available=True,
                reason="no API key: requests are throttled and may fail",
                needs_key=True,
                key_setting=self.meta.key_setting,
                signup_url=self.meta.signup_url,
            )
        return ProviderAvailability(available=True)

    def _headers(self) -> dict[str, str]:
        key = self.api_key()
        return {"x-api-key": key} if key else {}

    def _rate(self) -> dict[str, Any]:
        """Request policy, tightened when no key is configured.

        Keyless traffic gets a wide interval and a single retry: the API answers
        429 immediately under load (verified live), and retrying an unauthorised
        client repeatedly just wastes the search's time budget.
        """
        has_key = bool(self.api_key())
        return {
            "min_interval_s": 0.4 if has_key else self.rate_limit.min_interval_s,
            "concurrency": 2 if has_key else 1,
            "max_retries": 3 if has_key else self.rate_limit.max_retries,
        }

    async def search(self, request: SearchRequest, limit: int) -> list[Paper]:
        if request.mode in ("idea", "paper") and request.seed_text.strip():
            papers = await self._search_semantic(request, limit)
            if papers:
                return papers
            # Fall through to keyword search when the semantic endpoint is
            # unavailable (it is the first to be throttled).
        return await self._search_keyword(request, limit)

    async def _search_keyword(self, request: SearchRequest, limit: int) -> list[Paper]:
        collected: dict[str, Paper] = {}
        for query_text in (request.effective_queries() or [request.query])[
            : self.rate_limit.max_queries
        ]:
            if not query_text.strip():
                continue
            offset = 0
            while len(collected) < limit:
                params: dict[str, Any] = {
                    "query": query_text,
                    "limit": min(100, limit - len(collected)),
                    "offset": offset,
                    "fields": _FIELDS,
                }
                if request.year_from or request.year_to:
                    params["year"] = f"{request.year_from or ''}-{request.year_to or ''}"
                if request.open_access_only:
                    params["openAccessPdf"] = ""
                if request.venues:
                    params["venue"] = ",".join(request.venues[:5])
                if request.fields_of_study:
                    params["fieldsOfStudy"] = ",".join(request.fields_of_study[:5])

                body = await self.client.get_json(
                    self.id, f"{_BASE}/paper/search", params=params,
                    headers=self._headers(), **self._rate(),
                )
                data = (body or {}).get("data") or []
                if not data:
                    break
                for item in data:
                    paper = _parse_paper(item)
                    if paper is None:
                        continue
                    key = paper.s2_id or paper.doi or paper.title.lower()
                    collected.setdefault(key, paper)
                offset = (body or {}).get("next") or 0
                if not offset:
                    break
        return list(collected.values())[:limit]

    async def _search_semantic(self, request: SearchRequest, limit: int) -> list[Paper]:
        """Recommendation-based retrieval from a seed idea or paper.

        Strategy: try ``/paper/search/match`` to resolve the seed to a known
        paper, then ask for recommendations from it. If the seed is an idea with
        no matching paper, fall back to relevance search over the seed text.
        """
        seed = collapse_ws(request.seed_text)[:600]
        if not seed:
            return []
        seed_id = ""
        try:
            match = await self.client.get_json(
                self.id, f"{_BASE}/paper/search/match",
                params={"query": seed[:300], "fields": "paperId,title"},
                headers=self._headers(), **self._rate(),
            )
            candidates = (match or {}).get("data") or []
            if candidates:
                seed_id = str(candidates[0].get("paperId") or "")
        except Exception as exc:  # noqa: BLE001 - optional enhancement
            log.debug("S2 match failed (%s); using relevance search", exc)

        if seed_id:
            try:
                body = await self.client.get_json(
                    self.id,
                    f"https://api.semanticscholar.org/recommendations/v1/papers/"
                    f"forpaper/{seed_id}",
                    params={"limit": min(limit, 100), "fields": _FIELDS},
                    headers=self._headers(), **self._rate(),
                )
                papers = [
                    p for p in (
                        _parse_paper(item)
                        for item in ((body or {}).get("recommendedPapers") or [])
                    ) if p is not None
                ]
                if papers:
                    return papers[:limit]
            except Exception as exc:  # noqa: BLE001
                log.debug("S2 recommendations failed (%s)", exc)
        return []

    async def fetch_by_id(self, external_id: str) -> Paper | None:
        identifier = external_id.strip()
        if not identifier:
            return None
        doi = normalize_doi(identifier)
        arxiv = normalize_arxiv_id(identifier)
        if doi:
            key = f"DOI:{doi}"
        elif arxiv and arxiv != identifier.lower():
            key = f"ARXIV:{arxiv}"
        elif identifier.isdigit():
            key = f"PMID:{identifier}"
        else:
            key = identifier
        body = await self.client.get_json(
            self.id, f"{_BASE}/paper/{key}", params={"fields": _FIELDS},
            headers=self._headers(), **self._rate(),
        )
        return _parse_paper(body) if isinstance(body, dict) else None

"""DOAJ provider (Directory of Open Access Journals).

API: ``https://doaj.org/api/search/articles/{query}``
Docs: https://doaj.org/api/v4/docs

Narrow but useful: everything in DOAJ is, by definition, in a vetted fully
open-access journal, so every hit has a legally readable full text. When the
user checks "open access only" this is the highest-precision source, and it
covers disciplines (humanities, regional journals) that the CS-centric sources
miss entirely.

Query strings are Elasticsearch-flavoured and go in the *URL path*, not a
parameter - the only source here with that shape.
"""

from __future__ import annotations

from urllib.parse import quote
from typing import Any

from ...core.logging_setup import get_logger
from ...core.models import Author, Paper, SearchRequest
from ...core.util import coerce_int, collapse_ws, normalize_doi
from ..base import Provider, ProviderCapabilities, ProviderMeta, RateLimit

log = get_logger(__name__)

_BASE = "https://doaj.org/api/search/articles"


def _parse_article(entry: dict[str, Any]) -> Paper | None:
    bibjson = entry.get("bibjson") or {}
    title = collapse_ws(bibjson.get("title") or "")
    if not title:
        return None

    doi = ""
    for identifier in bibjson.get("identifier") or []:
        if str(identifier.get("type")).lower() == "doi":
            doi = normalize_doi(identifier.get("id"))
            break

    journal = bibjson.get("journal") or {}
    authors = [
        Author(
            name=collapse_ws(a.get("name") or ""),
            affiliation=collapse_ws(a.get("affiliation") or "")[:200],
            orcid=str(a.get("orcid_id") or "").rsplit("/", 1)[-1],
        )
        for a in (bibjson.get("author") or []) if a.get("name")
    ]

    url = ""
    pdf_url = ""
    for link in bibjson.get("link") or []:
        if link.get("type") == "fulltext" and link.get("url"):
            url = link["url"]
            if str(link.get("content_type") or "").lower() == "pdf":
                pdf_url = link["url"]

    keywords = [collapse_ws(k) for k in (bibjson.get("keywords") or []) if k]
    subjects = [
        collapse_ws(s.get("term") or "") for s in (bibjson.get("subject") or [])
    ]

    return Paper(
        title=title,
        abstract=collapse_ws(bibjson.get("abstract") or ""),
        authors=authors,
        year=coerce_int(bibjson.get("year"), 0) or None,
        venue=collapse_ws(journal.get("title") or ""),
        venue_type="journal",
        doi=doi,
        url=url or (f"https://doi.org/{doi}" if doi else ""),
        pdf_url=pdf_url,
        is_open_access=True,  # by definition of the directory
        fields_of_study=[s for s in subjects if s][:6],
        keywords=keywords[:10],
        language=(journal.get("language") or [""])[0].lower()[:2],
        raw={"doaj": {
            "publisher": journal.get("publisher"),
            "volume": journal.get("volume"),
            "number": journal.get("number"),
            "country": journal.get("country"),
            "start_page": bibjson.get("start_page"),
            "end_page": bibjson.get("end_page"),
        }},
    )


class DoajProvider(Provider):
    meta = ProviderMeta(
        id="doaj",
        name="DOAJ",
        name_zh="DOAJ 开放期刊目录",
        description="Vetted fully open-access journal articles across all "
                    "disciplines, including humanities and regional journals.",
        description_zh="经审核的完全开放获取期刊论文，覆盖全学科，含人文与区域性期刊。",
        homepage="https://doaj.org",
        docs_url="https://doaj.org/api/v4/docs",
        tier="free",
        coverage="~10M open-access articles",
        disciplines=["all", "humanities", "social sciences"],
    )
    capabilities = ProviderCapabilities(
        full_text_search=True,
        field_search=True,
        boolean_operators=True,
        year_range=True,
        open_access_filter=True,   # everything is OA
        venue_filter=True,
        author_filter=True,
        sort_by_date=True,
        returns_abstract=True,
        returns_pdf_url=True,
        max_results_per_request=100,
        supports_pagination=True,
    )
    rate_limit = RateLimit(min_interval_s=0.5, max_concurrency=2, max_queries=2)

    def _build_query(self, request: SearchRequest, query_text: str) -> str:
        clauses: list[str] = []
        if query_text.strip():
            clauses.append(f"({query_text.strip()})")
        for author in request.authors[:2]:
            clauses.append(f'bibjson.author.name:"{author}"')
        for venue in request.venues[:2]:
            clauses.append(f'bibjson.journal.title:"{venue}"')
        if request.year_from or request.year_to:
            start = request.year_from or 1800
            end = request.year_to or 3000
            clauses.append(f"bibjson.year:[{start} TO {end}]")
        return " AND ".join(clauses) if clauses else "*"

    async def search(self, request: SearchRequest, limit: int) -> list[Paper]:
        queries = request.effective_queries() or [request.query]
        if request.mode in ("idea", "paper") and request.seed_text:
            if not request.expanded_queries:
                queries = [collapse_ws(request.seed_text)[:300]]

        collected: dict[str, Paper] = {}
        for query_text in queries[: self.rate_limit.max_queries]:
            page = 1
            while len(collected) < limit:
                page_size = min(100, limit - len(collected))
                query_string = quote(self._build_query(request, query_text), safe="")
                params: dict[str, Any] = {"page": page, "pageSize": page_size}
                if request.sort == "date":
                    params["sort"] = "bibjson.year:desc"
                body = await self.client.get_json(
                    self.id, f"{_BASE}/{query_string}", params=params,
                    **self.rate_limit.request_kwargs(),
                )
                results = (body or {}).get("results") or []
                if not results:
                    break
                for entry in results:
                    paper = _parse_article(entry)
                    if paper is None:
                        continue
                    key = paper.doi or paper.title.lower()
                    collected.setdefault(key, paper)
                total = coerce_int((body or {}).get("total"), 0)
                if page * page_size >= min(total, limit) or len(results) < page_size:
                    break
                page += 1
        return list(collected.values())[:limit]

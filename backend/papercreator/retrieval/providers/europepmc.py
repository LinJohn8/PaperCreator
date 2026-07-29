"""Europe PMC provider.

API: ``https://www.ebi.ac.uk/europepmc/webservices/rest/search``
Docs: https://europepmc.org/RestfulWebService

Europe PMC mirrors PubMed and adds preprints, patents, clinical guidelines and
full-text-mined content, all through **one request** (unlike PubMed's
esearch+efetch round trip) with no key required. That makes it the fastest free
life-sciences source and a good general fallback: its ``resultType=core``
response carries abstracts, citation counts and open-access status together.

Query syntax supports fielded search (``TITLE:``, ``AUTH:``, ``PUB_YEAR:``) and
boolean operators, so most of the request filters map server-side.
"""

from __future__ import annotations

from typing import Any

from ...core.logging_setup import get_logger
from ...core.models import Author, Paper, SearchRequest
from ...core.util import coerce_int, collapse_ws, normalize_doi
from ..base import Provider, ProviderCapabilities, ProviderMeta, RateLimit

log = get_logger(__name__)

_ENDPOINT = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

_TYPE_MAP = {
    "Journal Article": "journal", "Preprint": "preprint", "Review": "review",
    "Book": "book", "Patent": "patent", "Conference Abstract": "conference",
}


def _parse_result(item: dict[str, Any]) -> Paper | None:
    title = collapse_ws(item.get("title") or "").rstrip(".")
    if not title:
        return None

    authors: list[Author] = []
    for entry in ((item.get("authorList") or {}).get("author") or []):
        name = collapse_ws(
            entry.get("fullName") or
            f"{entry.get('firstName', '')} {entry.get('lastName', '')}"
        )
        if name:
            affiliation = collapse_ws(entry.get("affiliation") or "")
            authors.append(Author(name=name, affiliation=affiliation[:200]))
    if not authors and item.get("authorString"):
        authors = [
            Author(name=collapse_ws(part))
            for part in str(item["authorString"]).split(",")
            if collapse_ws(part)
        ][:30]

    full_text_urls = (item.get("fullTextUrlList") or {}).get("fullTextUrl") or []
    pdf_url = ""
    for entry in full_text_urls:
        if str(entry.get("documentStyle")) == "pdf" and entry.get("url"):
            pdf_url = entry["url"]
            break

    keywords = [
        collapse_ws(k) for k in ((item.get("keywordList") or {}).get("keyword") or [])
    ]
    mesh = [
        collapse_ws((h.get("descriptorName") or ""))
        for h in ((item.get("meshHeadingList") or {}).get("meshHeading") or [])
    ]

    pmcid = str(item.get("pmcid") or "")
    return Paper(
        title=title,
        abstract=collapse_ws(item.get("abstractText") or ""),
        authors=authors,
        year=coerce_int(item.get("pubYear"), 0) or None,
        venue=collapse_ws(
            item.get("journalTitle")
            or ((item.get("journalInfo") or {}).get("journal") or {}).get("title")
            or item.get("bookOrReportDetails", {}).get("publisher", "")
            or ""
        ),
        venue_type=_TYPE_MAP.get(str(item.get("pubType") or "").title(), ""),
        doi=normalize_doi(item.get("doi")),
        pmid=str(item.get("pmid") or ""),
        url=(
            f"https://europepmc.org/article/{item.get('source', 'MED')}/"
            f"{item.get('id', '')}"
        ),
        pdf_url=pdf_url,
        is_open_access=str(item.get("isOpenAccess") or "N").upper() == "Y",
        citation_count=coerce_int(item.get("citedByCount"), 0),
        fields_of_study=[m for m in mesh if m][:8],
        keywords=[k for k in ([*keywords, *mesh]) if k][:10],
        language=str(item.get("language") or "").lower()[:2],
        raw={"europepmc": {
            "source": item.get("source"), "pmcid": pmcid,
            "hasFullText": item.get("hasTextMinedTerms"),
            "pubType": item.get("pubType"),
        }},
    )


class EuropePmcProvider(Provider):
    meta = ProviderMeta(
        id="europepmc",
        name="Europe PMC",
        name_zh="Europe PMC",
        description="Life-sciences literature including preprints and patents. "
                    "Abstracts, citation counts and OA status in one request, "
                    "no API key.",
        description_zh="生命科学文献（含预印本与专利），单次请求即返回摘要、引用数与开放获取状态，无需 key。",
        homepage="https://europepmc.org",
        docs_url="https://europepmc.org/RestfulWebService",
        tier="free",
        coverage="~44M records",
        disciplines=["medicine", "biology", "life sciences", "chemistry"],
    )
    capabilities = ProviderCapabilities(
        full_text_search=True,
        field_search=True,
        boolean_operators=True,
        year_range=True,
        open_access_filter=True,
        venue_filter=True,
        author_filter=True,
        sort_by_date=True,
        sort_by_citations=True,
        returns_abstract=True,
        returns_citations=True,
        returns_pdf_url=True,
        max_results_per_request=1000,
        supports_pagination=True,
    )
    rate_limit = RateLimit(min_interval_s=0.15, max_concurrency=3, max_queries=2)

    def _build_query(self, request: SearchRequest, query_text: str) -> str:
        clauses: list[str] = []
        if query_text.strip():
            clauses.append(f"({query_text.strip()})")
        for author in request.authors[:3]:
            clauses.append(f'AUTH:"{author}"')
        for venue in request.venues[:2]:
            clauses.append(f'JOURNAL:"{venue}"')
        if request.year_from and request.year_to:
            clauses.append(f"PUB_YEAR:[{request.year_from} TO {request.year_to}]")
        elif request.year_from:
            clauses.append(f"PUB_YEAR:[{request.year_from} TO 3000]")
        elif request.year_to:
            clauses.append(f"PUB_YEAR:[1800 TO {request.year_to}]")
        if request.open_access_only:
            clauses.append("OPEN_ACCESS:Y")
        for excluded in request.exclude_keywords[:3]:
            clauses.append(f'NOT "{excluded}"')
        return " AND ".join(c for c in clauses if not c.startswith("NOT")) + "".join(
            f" {c}" for c in clauses if c.startswith("NOT")
        )

    async def search(self, request: SearchRequest, limit: int) -> list[Paper]:
        queries = request.effective_queries() or [request.query]
        if request.mode in ("idea", "paper") and request.seed_text:
            if not request.expanded_queries:
                queries = [collapse_ws(request.seed_text)[:400]]

        sort = ""
        if request.sort == "date":
            sort = "P_PDATE_D desc"
        elif request.sort == "citations":
            sort = "CITED desc"

        collected: dict[str, Paper] = {}
        for query_text in queries[: self.rate_limit.max_queries]:
            cursor = "*"
            while len(collected) < limit:
                page_size = min(100, limit - len(collected))
                params: dict[str, Any] = {
                    "query": self._build_query(request, query_text),
                    "format": "json",
                    "resultType": "core",
                    "pageSize": page_size,
                    "cursorMark": cursor,
                }
                if sort:
                    params["sort"] = sort
                body = await self.client.get_json(
                    self.id, _ENDPOINT, params=params,
                    **self.rate_limit.request_kwargs(),
                )
                results = ((body or {}).get("resultList") or {}).get("result") or []
                if not results:
                    break
                for item in results:
                    paper = _parse_result(item)
                    if paper is None:
                        continue
                    key = paper.doi or paper.pmid or paper.title.lower()
                    collected.setdefault(key, paper)
                next_cursor = (body or {}).get("nextCursorMark") or ""
                if not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
        return list(collected.values())[:limit]

    async def fetch_by_id(self, external_id: str) -> Paper | None:
        identifier = external_id.strip()
        if not identifier:
            return None
        doi = normalize_doi(identifier)
        query = f'DOI:"{doi}"' if doi else (
            f"EXT_ID:{identifier}" if identifier.isdigit() else f'"{identifier}"'
        )
        body = await self.client.get_json(
            self.id, _ENDPOINT,
            params={"query": query, "format": "json", "resultType": "core",
                    "pageSize": 1},
            **self.rate_limit.request_kwargs(),
        )
        results = ((body or {}).get("resultList") or {}).get("result") or []
        return _parse_result(results[0]) if results else None

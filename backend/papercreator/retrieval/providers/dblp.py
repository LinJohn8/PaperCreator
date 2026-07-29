"""DBLP provider.

API: ``https://dblp.org/search/publ/api``
Docs: https://dblp.org/faq/How+to+use+the+dblp+search+API.html

DBLP is the computer-science bibliography. It has the cleanest venue data in the
field - proper conference names and series, which matter because CS publishes at
conferences and most general indexes record those badly or not at all.

It carries **no abstracts and no citation counts**, so on its own it is poor
input for the semantic landscape. Its role here is complementary: DBLP hits are
merged with OpenAlex/Crossref records by DOI, contributing authoritative venue
strings to a record whose abstract came from elsewhere. That is exactly what the
merge logic in ``store.papers.merge_papers`` is built for.

DBLP's query language does prefix matching (``graph neural`` becomes
``graph* neural*``), which is why a short query returns broad results.
"""

from __future__ import annotations

from typing import Any

from ...core.logging_setup import get_logger
from ...core.models import Author, Paper, SearchRequest
from ...core.util import coerce_int, collapse_ws, normalize_doi
from ..base import Provider, ProviderCapabilities, ProviderMeta, RateLimit

log = get_logger(__name__)

_ENDPOINT = "https://dblp.org/search/publ/api"

_TYPE_MAP = {
    "Conference and Workshop Papers": "conference",
    "Journal Articles": "journal",
    "Informal and Other Publications": "preprint",
    "Books and Theses": "book",
    "Parts in Books or Collections": "book",
    "Editorship": "editorial",
}


def _as_list(value: Any) -> list[Any]:
    """DBLP returns a bare object when a list has one element."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_hit(hit: dict[str, Any]) -> Paper | None:
    info = hit.get("info") or {}
    title = collapse_ws(info.get("title") or "").rstrip(".")
    if not title:
        return None

    authors: list[Author] = []
    author_block = (info.get("authors") or {}).get("author")
    for entry in _as_list(author_block):
        if isinstance(entry, dict):
            name = collapse_ws(entry.get("text") or "")
        else:
            name = collapse_ws(str(entry))
        # DBLP disambiguates homonyms with a trailing number: "Wei Wang 0001".
        if name and name.split()[-1].isdigit() and len(name.split()[-1]) == 4:
            name = " ".join(name.split()[:-1])
        if name:
            authors.append(Author(name=name))

    venue_parts = [collapse_ws(str(v)) for v in _as_list(info.get("venue")) if v]
    doi = normalize_doi(info.get("doi"))
    return Paper(
        title=title,
        # DBLP has no abstracts; leaving this empty is correct and lets the
        # merge take an abstract from another provider.
        abstract="",
        authors=authors,
        year=coerce_int(info.get("year"), 0) or None,
        venue=" / ".join(venue_parts),
        venue_type=_TYPE_MAP.get(str(info.get("type") or ""), ""),
        doi=doi,
        url=info.get("ee") or info.get("url") or "",
        is_open_access=bool(info.get("access") == "open"),
        raw={"dblp": {
            "key": info.get("key"),
            "type": info.get("type"),
            "volume": info.get("volume"),
            "number": info.get("number"),
            "pages": info.get("pages"),
            "publisher": info.get("publisher"),
        }},
    )


class DblpProvider(Provider):
    meta = ProviderMeta(
        id="dblp",
        name="DBLP",
        name_zh="DBLP 计算机文献库",
        description="Computer-science bibliography with authoritative conference "
                    "and journal names. No abstracts or citation counts.",
        description_zh="计算机领域文献库，会议/期刊名称最准确；不提供摘要与引用数。",
        homepage="https://dblp.org",
        docs_url="https://dblp.org/faq/How+to+use+the+dblp+search+API.html",
        tier="free",
        coverage="~7M CS publications",
        disciplines=["computer science"],
    )
    capabilities = ProviderCapabilities(
        full_text_search=True,
        field_search=False,
        boolean_operators=False,
        year_range=False,          # no server-side date filter; pipeline filters
        author_filter=True,
        sort_by_date=False,
        returns_abstract=False,
        max_results_per_request=1000,
        supports_pagination=True,
    )
    # DBLP is the most throttle-sensitive source here. Observed live: after a
    # short burst it stops answering entirely (connection refused, not 429), and
    # the block persists for minutes. So: one query variant, a wide interval,
    # and no retries - failing this search fast is much better than getting the
    # host to blacklist us for the rest of the session.
    rate_limit = RateLimit(
        min_interval_s=2.5, max_concurrency=1, max_retries=0, max_queries=1,
        note="blocks bursts aggressively; 1 query per search, no retries",
    )

    async def search(self, request: SearchRequest, limit: int) -> list[Paper]:
        queries = request.effective_queries() or [request.query]
        if request.mode in ("idea", "paper") and request.seed_text:
            if not request.expanded_queries:
                # A long seed produces a useless prefix query; use the leading
                # phrase only.
                queries = [collapse_ws(request.seed_text)[:120]]
        terms = [*queries]
        for author in request.authors[:2]:
            terms.append(author)

        collected: dict[str, Paper] = {}
        for query_text in terms[: self.rate_limit.max_queries]:
            if not query_text.strip():
                continue
            offset = 0
            while len(collected) < limit:
                batch = min(1000, limit - len(collected))
                body = await self.client.get_json(
                    self.id, _ENDPOINT,
                    params={"q": query_text, "format": "json", "h": batch,
                            "f": offset, "c": 0},
                    **self.rate_limit.request_kwargs(),
                )
                result = (body or {}).get("result") or {}
                hits_block = result.get("hits") or {}
                hits = _as_list(hits_block.get("hit"))
                if not hits:
                    break
                for hit in hits:
                    paper = _parse_hit(hit) if isinstance(hit, dict) else None
                    if paper is None:
                        continue
                    if request.year_from and (paper.year or 0) < request.year_from:
                        continue
                    if request.year_to and (paper.year or 9999) > request.year_to:
                        continue
                    key = paper.doi or f"{paper.title.lower()}|{paper.year}"
                    collected.setdefault(key, paper)
                offset += len(hits)
                total = coerce_int(hits_block.get("@total"), 0)
                if offset >= total or len(hits) < batch:
                    break
        return list(collected.values())[:limit]

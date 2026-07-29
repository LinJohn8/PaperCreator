"""OpenAlex provider.

API: ``https://api.openalex.org/works``
Docs: https://docs.openalex.org / https://developers.openalex.org

Why it matters: OpenAlex is the broadest free index (250M+ works across every
discipline) and the only free source here that returns **citation counts and
reference lists together**, which is what makes the citation-graph half of the
analysis possible.

Two behaviours to know:

* **Abstracts arrive as an inverted index** (``{"word": [positions]}``) rather
  than plain text. :func:`reconstruct_abstract` rebuilds the string; a paper
  with no ``abstract_inverted_index`` genuinely has no abstract on record.
* **Access is credit-metered.** Verified live: a keyless request succeeds and
  the response reports ``meta.cost_usd`` (~$0.001/query) against a small free
  daily allowance; a free API key raises that allowance about tenfold. The
  provider therefore stays usable without a key (``tier="freemium"``) and only
  attaches ``api_key`` when the user has configured one.
"""

from __future__ import annotations

from typing import Any

from ...core.logging_setup import get_logger
from ...core.models import Author, Paper, SearchRequest
from ...core.util import collapse_ws, normalize_doi
from ..base import Provider, ProviderCapabilities, ProviderMeta, RateLimit

log = get_logger(__name__)

_ENDPOINT = "https://api.openalex.org/works"

# Explicit field selection keeps responses ~5x smaller than the default payload.
_SELECT = ",".join([
    "id", "doi", "title", "display_name", "publication_year", "publication_date",
    "type", "cited_by_count", "referenced_works_count", "referenced_works",
    "language", "abstract_inverted_index", "authorships", "primary_location",
    "open_access", "topics", "keywords", "ids", "is_retracted",
])

_TYPE_MAP = {
    "article": "journal", "journal-article": "journal", "preprint": "preprint",
    "book": "book", "book-chapter": "book", "dissertation": "thesis",
    "proceedings-article": "conference", "posted-content": "preprint",
    "review": "journal", "dataset": "dataset", "report": "report",
}


def reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str:
    """Rebuild plain text from OpenAlex's inverted index.

    ``{"We": [0], "study": [1]}`` -> ``"We study"``. Gaps in the position
    sequence are possible (dropped stopwords) and are simply skipped, which is
    what OpenAlex's own reconstruction does.
    """
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, indices in inverted.items():
        if not isinstance(indices, list):
            continue
        for index in indices:
            if isinstance(index, int):
                positions.append((index, word))
    if not positions:
        return ""
    positions.sort(key=lambda pair: pair[0])
    return collapse_ws(" ".join(word for _, word in positions))


def _strip_openalex_id(value: Any) -> str:
    """``https://openalex.org/W2116341502`` -> ``W2116341502``."""
    if not value:
        return ""
    return str(value).rstrip("/").rsplit("/", 1)[-1]


def _parse_work(work: dict[str, Any]) -> Paper | None:
    title = collapse_ws(work.get("title") or work.get("display_name") or "")
    if not title:
        return None

    authors: list[Author] = []
    for authorship in work.get("authorships") or []:
        person = authorship.get("author") or {}
        name = collapse_ws(person.get("display_name") or authorship.get("raw_author_name") or "")
        if not name:
            continue
        institutions = authorship.get("institutions") or []
        affiliation = collapse_ws(
            (institutions[0].get("display_name") if institutions else "")
            or (authorship.get("raw_affiliation_strings") or [""])[0]
        )
        orcid = str(person.get("orcid") or "").rsplit("/", 1)[-1]
        authors.append(Author(name=name, affiliation=affiliation[:200], orcid=orcid))

    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    open_access = work.get("open_access") or {}
    pdf_url = location.get("pdf_url") or open_access.get("oa_url") or ""

    topics = [
        collapse_ws(t.get("display_name") or "")
        for t in (work.get("topics") or [])
        if t.get("display_name")
    ]
    keywords = [
        collapse_ws(k.get("display_name") or "")
        for k in (work.get("keywords") or [])
        if isinstance(k, dict) and k.get("display_name")
    ]

    ids = work.get("ids") or {}
    pmid = str(ids.get("pmid") or "").rsplit("/", 1)[-1]

    return Paper(
        title=title,
        abstract=reconstruct_abstract(work.get("abstract_inverted_index")),
        authors=authors,
        year=work.get("publication_year"),
        venue=collapse_ws(source.get("display_name") or ""),
        venue_type=_TYPE_MAP.get(str(work.get("type") or ""), ""),
        doi=normalize_doi(work.get("doi")),
        openalex_id=_strip_openalex_id(work.get("id")),
        pmid=pmid,
        url=location.get("landing_page_url") or str(work.get("id") or ""),
        pdf_url=pdf_url or "",
        is_open_access=bool(open_access.get("is_oa")),
        citation_count=int(work.get("cited_by_count") or 0),
        reference_count=int(work.get("referenced_works_count") or 0),
        fields_of_study=topics[:6],
        keywords=keywords[:8],
        # Reference ids power the citation graph; capped because a survey can
        # list 500+ and the full list bloats the row.
        references_ids=[
            _strip_openalex_id(r) for r in (work.get("referenced_works") or [])[:200]
        ],
        language=str(work.get("language") or ""),
        raw={"openalex": {
            "type": work.get("type"),
            "oa_status": open_access.get("oa_status"),
            "is_retracted": work.get("is_retracted"),
            "publication_date": work.get("publication_date"),
        }},
    )


class OpenAlexProvider(Provider):
    meta = ProviderMeta(
        id="openalex",
        name="OpenAlex",
        name_zh="OpenAlex 学术图谱",
        description="Broadest free scholarly index (250M+ works, all "
                    "disciplines). Returns citation counts and reference lists.",
        description_zh="覆盖最广的免费学术索引（2.5亿+，全学科），提供引用数与参考文献列表。",
        homepage="https://openalex.org",
        docs_url="https://docs.openalex.org",
        tier="freemium",
        coverage="~250M works, all disciplines",
        disciplines=["all"],
        requires_key=False,
        key_setting="openalex",
        signup_url="https://openalex.org/pricing",
    )
    capabilities = ProviderCapabilities(
        full_text_search=True,
        field_search=True,
        boolean_operators=False,
        year_range=True,
        open_access_filter=True,
        venue_filter=True,
        author_filter=True,
        fields_of_study_filter=True,
        sort_by_date=True,
        sort_by_citations=True,
        returns_abstract=True,
        returns_citations=True,
        returns_references=True,
        returns_pdf_url=True,
        max_results_per_request=100,
        supports_pagination=True,
    )
    rate_limit = RateLimit(
        min_interval_s=0.12, max_concurrency=4, max_queries=3,
        note="credit-metered; a free API key raises the daily allowance ~10x",
    )

    def _auth_params(self) -> dict[str, str]:
        key = self.api_key()
        return {"api_key": key} if key else {}

    def _endpoint(self) -> str:
        """Configured HTTPS mirror/proxy, or loopback endpoint for development."""
        from ...core.config import get_settings

        return get_settings().retrieval.openalex_endpoint

    def _build_filters(self, request: SearchRequest) -> str:
        """OpenAlex ``filter`` expression: comma-separated AND, ``|`` for OR."""
        filters: list[str] = []
        if request.year_from and request.year_to:
            filters.append(f"publication_year:{request.year_from}-{request.year_to}")
        elif request.year_from:
            filters.append(f"from_publication_date:{request.year_from}-01-01")
        elif request.year_to:
            filters.append(f"to_publication_date:{request.year_to}-12-31")
        if request.open_access_only:
            filters.append("is_oa:true")
        if request.authors:
            joined = "|".join(a.replace(",", " ") for a in request.authors[:3])
            filters.append(f"raw_author_name.search:{joined}")
        if request.venues:
            joined = "|".join(v.replace(",", " ") for v in request.venues[:3])
            filters.append(f"primary_location.source.display_name.search:{joined}")
        # Exclude paratext (editorials, tables of contents) and retractions -
        # they pollute a topical landscape.
        filters.append("is_paratext:false")
        return ",".join(filters)

    async def search(self, request: SearchRequest, limit: int) -> list[Paper]:
        endpoint = self._endpoint()
        queries = request.effective_queries() or [request.query]
        if request.mode in ("idea", "paper") and request.seed_text:
            # OpenAlex's `search` is BM25-ish over full text; a long abstract
            # works acceptably as a bag of words, so the seed is usable directly.
            queries = [collapse_ws(request.seed_text)[:1200], *queries][:3]

        sort = {
            "date": "publication_date:desc",
            "citations": "cited_by_count:desc",
            "relevance": "relevance_score:desc",
        }.get(request.sort, "relevance_score:desc")

        collected: dict[str, Paper] = {}
        per_page = min(200, self.capabilities.max_results_per_request)
        for query_text in queries[: self.rate_limit.max_queries]:
            remaining = limit - len(collected)
            if remaining <= 0:
                break
            cursor = "*"
            while remaining > 0:
                params: dict[str, Any] = {
                    "search": query_text,
                    "per-page": min(per_page, remaining),
                    "select": _SELECT,
                    "cursor": cursor,
                    **self._auth_params(),
                }
                filter_expr = self._build_filters(request)
                if filter_expr:
                    params["filter"] = filter_expr
                # relevance_score is only defined for `search` queries.
                if sort != "relevance_score:desc" or not query_text:
                    params["sort"] = sort

                body = await self.client.get_json(
                    self.id, endpoint, params=params,
                    **self.rate_limit.request_kwargs(),
                )
                results = body.get("results") or []
                if not results:
                    break
                for work in results:
                    if work.get("is_retracted"):
                        continue
                    paper = _parse_work(work)
                    if paper is None:
                        continue
                    key = paper.doi or paper.openalex_id or paper.title.lower()
                    if key not in collected:
                        collected[key] = paper
                remaining = limit - len(collected)
                cursor = ((body.get("meta") or {}).get("next_cursor")) or ""
                if not cursor:
                    break
        return list(collected.values())[:limit]

    async def fetch_by_id(self, external_id: str) -> Paper | None:
        """Accepts a DOI, an OpenAlex id (``W...``) or a PMID."""
        identifier = external_id.strip()
        if not identifier:
            return None
        doi = normalize_doi(identifier)
        endpoint = self._endpoint()
        if doi:
            path = f"{endpoint}/doi:{doi}"
        elif identifier.upper().startswith("W"):
            path = f"{endpoint}/{identifier}"
        elif identifier.isdigit():
            path = f"{endpoint}/pmid:{identifier}"
        else:
            return None
        body = await self.client.get_json(
            self.id, path, params={"select": _SELECT, **self._auth_params()},
            **self.rate_limit.request_kwargs(),
        )
        return _parse_work(body) if isinstance(body, dict) else None

    async def fetch_citations(self, openalex_id: str, limit: int = 100) -> list[Paper]:
        """Papers citing the given work - used to expand a seed paper's frontier."""
        body = await self.client.get_json(
            self.id, self._endpoint(),
            params={
                "filter": f"cites:{openalex_id}",
                "per-page": min(limit, 100),
                "select": _SELECT,
                "sort": "cited_by_count:desc",
                **self._auth_params(),
            },
            **self.rate_limit.request_kwargs(),
        )
        out = []
        for work in body.get("results") or []:
            paper = _parse_work(work)
            if paper is not None:
                out.append(paper)
        return out

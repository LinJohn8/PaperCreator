"""Crossref provider.

API: ``https://api.crossref.org/works``
Docs: https://api.crossref.org/swagger-ui/index.html

Crossref is the DOI registration agency, so it has near-complete coverage of
*published* literature with authoritative venue metadata - the best free source
for the bibliography (publisher, journal, volume, pages, ISSN) that LaTeX/BibTeX
needs. Its weakness is abstracts: only a minority of records include one, and
they arrive as JATS XML fragments, so :func:`clean_jats` strips the markup.

Politeness: Crossref asks clients to identify themselves via a ``mailto``. Doing
so routes the request to a better-resourced pool, so the configured contact
email is attached to every request when present.
"""

from __future__ import annotations

import re
from typing import Any

from ...core.config import get_settings
from ...core.logging_setup import get_logger
from ...core.models import Author, Paper, SearchRequest
from ...core.util import collapse_ws, normalize_doi
from ..base import Provider, ProviderCapabilities, ProviderMeta, RateLimit

log = get_logger(__name__)

_ENDPOINT = "https://api.crossref.org/works"

# Only fields Crossref's /works route actually accepts in `select`. Verified
# against the live API: an unsupported name (e.g. `language`) makes the whole
# request fail with HTTP 400 and a validation-failure body, so this list must
# not be extended speculatively.
_SELECT = ",".join([
    "DOI", "title", "abstract", "author", "container-title", "issued", "type",
    "is-referenced-by-count", "references-count", "URL", "link", "subject",
    "publisher", "volume", "issue", "page", "ISSN", "license",
    "short-container-title", "published", "score",
])

_TYPE_MAP = {
    "journal-article": "journal", "proceedings-article": "conference",
    "book-chapter": "book", "book": "book", "posted-content": "preprint",
    "dissertation": "thesis", "report": "report", "dataset": "dataset",
    "monograph": "book", "reference-entry": "reference",
}

_JATS_TAG = re.compile(r"<[^>]+>")
_ABSTRACT_LABEL = re.compile(r"^\s*abstract[:\s]*", re.IGNORECASE)


def clean_jats(text: str) -> str:
    """Strip JATS/XML markup from a Crossref abstract.

    Crossref abstracts look like
    ``<jats:p>We show...</jats:p>``, sometimes with titled sections. Tags are
    removed, entities decoded, and a leading "Abstract" label dropped.
    """
    if not text:
        return ""
    without_tags = _JATS_TAG.sub(" ", text)
    decoded = (
        without_tags.replace("&amp;", "&").replace("&lt;", "<")
        .replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'")
        .replace("&#x2018;", "'").replace("&#x2019;", "'")
    )
    return _ABSTRACT_LABEL.sub("", collapse_ws(decoded))


def _extract_year(item: dict[str, Any]) -> int | None:
    """Prefer ``issued`` (publication), fall back to online/print dates."""
    for field in ("issued", "published-print", "published-online", "created"):
        parts = ((item.get(field) or {}).get("date-parts") or [[]])[0]
        if parts and isinstance(parts[0], int):
            return parts[0]
    return None


def _parse_item(item: dict[str, Any]) -> Paper | None:
    titles = item.get("title") or []
    title = collapse_ws(titles[0]) if titles else ""
    if not title:
        return None

    authors: list[Author] = []
    for person in item.get("author") or []:
        given = collapse_ws(person.get("given") or "")
        family = collapse_ws(person.get("family") or "")
        name = f"{given} {family}".strip() or collapse_ws(person.get("name") or "")
        if not name:
            continue
        affiliations = person.get("affiliation") or []
        affiliation = collapse_ws(
            affiliations[0].get("name") if affiliations else ""
        )
        orcid = str(person.get("ORCID") or "").rsplit("/", 1)[-1]
        authors.append(Author(name=name, affiliation=affiliation[:200], orcid=orcid))

    containers = item.get("container-title") or []
    short_containers = item.get("short-container-title") or []
    venue = collapse_ws(containers[0] if containers else "")

    pdf_url = ""
    for link in item.get("link") or []:
        if link.get("content-type") in ("application/pdf", "unspecified") and link.get("URL"):
            pdf_url = link["URL"]
            if link.get("content-type") == "application/pdf":
                break

    licenses = item.get("license") or []
    is_oa = any(
        "creativecommons" in str(entry.get("URL", "")).lower() for entry in licenses
    )

    return Paper(
        title=title,
        abstract=clean_jats(item.get("abstract") or ""),
        authors=authors,
        year=_extract_year(item),
        venue=venue,
        venue_type=_TYPE_MAP.get(str(item.get("type") or ""), ""),
        doi=normalize_doi(item.get("DOI")),
        url=item.get("URL") or "",
        pdf_url=pdf_url,
        is_open_access=is_oa,
        citation_count=int(item.get("is-referenced-by-count") or 0),
        reference_count=int(item.get("references-count") or 0),
        fields_of_study=[collapse_ws(s) for s in (item.get("subject") or [])][:6],
        language=str(item.get("language") or ""),
        raw={"crossref": {
            "publisher": item.get("publisher"),
            "volume": item.get("volume"),
            "issue": item.get("issue"),
            "page": item.get("page"),
            "issn": item.get("ISSN"),
            "type": item.get("type"),
            "short_container": short_containers[0] if short_containers else "",
        }},
    )


class CrossrefProvider(Provider):
    meta = ProviderMeta(
        id="crossref",
        name="Crossref",
        name_zh="Crossref DOI 库",
        description="DOI registry with authoritative publication metadata "
                    "(journal, volume, pages, publisher). Abstracts are sparse.",
        description_zh="DOI 注册机构，出版元数据权威（期刊、卷、页码、出版社），摘要覆盖不全。",
        homepage="https://www.crossref.org",
        docs_url="https://api.crossref.org/swagger-ui/index.html",
        tier="free",
        coverage="~160M registered DOIs",
        disciplines=["all"],
    )
    capabilities = ProviderCapabilities(
        full_text_search=True,
        field_search=True,
        boolean_operators=False,
        year_range=True,
        venue_filter=True,
        author_filter=True,
        sort_by_date=True,
        sort_by_citations=True,
        # Only ~30% of records carry an abstract; the pipeline weights this.
        returns_abstract=False,
        returns_citations=True,
        max_results_per_request=100,
        supports_pagination=True,
    )
    rate_limit = RateLimit(
        min_interval_s=0.06, max_concurrency=4, max_queries=2,
        note="polite pool when a contact email is configured",
    )

    def _base_params(self) -> dict[str, Any]:
        email = get_settings().identity.contact_email.strip()
        return {"mailto": email} if email else {}

    async def search(self, request: SearchRequest, limit: int) -> list[Paper]:
        queries = request.effective_queries() or [request.query]
        if request.mode in ("idea", "paper") and request.seed_text:
            queries = [collapse_ws(request.seed_text)[:1000], *queries][:2]

        sort_params: dict[str, str] = {}
        if request.sort == "date":
            sort_params = {"sort": "published", "order": "desc"}
        elif request.sort == "citations":
            sort_params = {"sort": "is-referenced-by-count", "order": "desc"}

        # Crossref filters are AND-ed, so listing several `type:` values would
        # match nothing. Types are post-filtered by the pipeline instead; only
        # date / license / abstract filters go server-side.
        filters: list[str] = []
        if request.year_from:
            filters.append(f"from-pub-date:{request.year_from}-01-01")
        if request.year_to:
            filters.append(f"until-pub-date:{request.year_to}-12-31")
        if request.open_access_only:
            filters.append("has-license:true")
        filters.append("has-abstract:true" if request.mode in ("idea", "paper") else "")
        filter_expr = ",".join(f for f in filters if f)

        collected: dict[str, Paper] = {}
        for query_text in queries[: self.rate_limit.max_queries]:
            offset = 0
            while len(collected) < limit:
                rows = min(100, limit - len(collected))
                params: dict[str, Any] = {
                    "query.bibliographic": query_text,
                    "rows": rows,
                    "offset": offset,
                    "select": _SELECT,
                    **sort_params,
                    **self._base_params(),
                }
                if filter_expr:
                    params["filter"] = filter_expr
                if request.authors:
                    params["query.author"] = " ".join(request.authors[:3])
                if request.venues:
                    params["query.container-title"] = " ".join(request.venues[:2])

                body = await self.client.get_json(
                    self.id, _ENDPOINT, params=params,
                    **self.rate_limit.request_kwargs(),
                )
                items = ((body or {}).get("message") or {}).get("items") or []
                if not items:
                    break
                for item in items:
                    paper = _parse_item(item)
                    if paper is None:
                        continue
                    key = paper.doi or paper.title.lower()
                    if key not in collected:
                        collected[key] = paper
                offset += len(items)
                # Crossref caps deep offsets; beyond this use cursor paging,
                # which is unnecessary for interactive search sizes.
                if offset >= 1000 or len(items) < rows:
                    break
        return list(collected.values())[:limit]

    async def fetch_by_id(self, external_id: str) -> Paper | None:
        doi = normalize_doi(external_id)
        if not doi:
            return None
        body = await self.client.get_json(
            self.id, f"{_ENDPOINT}/{doi}", params=self._base_params(),
            **self.rate_limit.request_kwargs(),
        )
        message = (body or {}).get("message")
        return _parse_item(message) if isinstance(message, dict) else None

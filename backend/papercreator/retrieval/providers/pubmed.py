"""PubMed provider (NCBI E-utilities).

API: ``esearch.fcgi`` then ``efetch.fcgi``
Docs: https://www.ncbi.nlm.nih.gov/books/NBK25501/

PubMed is the authority for biomedical literature and the only free source here
with a curated controlled vocabulary (MeSH terms), which makes its keywords far
cleaner than machine-extracted ones - useful for the heatmap layers.

Two-step protocol: ``esearch`` returns PMIDs for a query, ``efetch`` returns the
records. That is inherent to E-utilities, not a design choice here.

Rate limits are enforced by NCBI *by IP*: 3 requests/second without an API key,
10 with one. Exceeding it earns a block, so the limiter is conservative.
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree

from ...core.errors import ProviderError
from ...core.logging_setup import get_logger
from ...core.models import Author, Paper, SearchRequest
from ...core.util import collapse_ws, normalize_doi
from ..base import Provider, ProviderCapabilities, ProviderMeta, RateLimit

log = get_logger(__name__)

_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
_ESEARCH = f"{_BASE}/esearch.fcgi"
_EFETCH = f"{_BASE}/efetch.fcgi"


def _text(element: ElementTree.Element | None, path: str, default: str = "") -> str:
    if element is None:
        return default
    found = element.find(path)
    if found is None:
        return default
    return collapse_ws("".join(found.itertext()))


def _parse_article(article: ElementTree.Element) -> Paper | None:
    citation = article.find("MedlineCitation")
    if citation is None:
        return None
    article_el = citation.find("Article")
    if article_el is None:
        return None

    title = _text(article_el, "ArticleTitle")
    if not title:
        return None

    # Structured abstracts have multiple labelled AbstractText children.
    abstract_parts: list[str] = []
    for text_el in article_el.findall("Abstract/AbstractText"):
        label = text_el.get("Label")
        body = collapse_ws("".join(text_el.itertext()))
        if not body:
            continue
        abstract_parts.append(f"{label}: {body}" if label else body)

    authors: list[Author] = []
    for author_el in article_el.findall("AuthorList/Author"):
        last = _text(author_el, "LastName")
        fore = _text(author_el, "ForeName")
        collective = _text(author_el, "CollectiveName")
        name = f"{fore} {last}".strip() or collective
        if not name:
            continue
        affiliation = _text(author_el, "AffiliationInfo/Affiliation")
        orcid = ""
        for identifier in author_el.findall("Identifier"):
            if identifier.get("Source") == "ORCID":
                orcid = collapse_ws(identifier.text or "").rsplit("/", 1)[-1]
        authors.append(Author(name=name, affiliation=affiliation[:200], orcid=orcid))

    year_text = (
        _text(article_el, "Journal/JournalIssue/PubDate/Year")
        or _text(article_el, "Journal/JournalIssue/PubDate/MedlineDate")[:4]
        or _text(article, "PubmedData/History/PubMedPubDate/Year")
    )
    year = int(year_text) if year_text[:4].isdigit() else None

    doi = ""
    pmc_id = ""
    for identifier in article.findall("PubmedData/ArticleIdList/ArticleId"):
        id_type = identifier.get("IdType")
        value = collapse_ws(identifier.text or "")
        if id_type == "doi":
            doi = normalize_doi(value)
        elif id_type == "pmc":
            pmc_id = value

    pmid = _text(citation, "PMID")
    mesh_terms = [
        _text(descriptor, ".")
        for descriptor in citation.findall("MeshHeadingList/MeshHeading/DescriptorName")
    ]
    keywords = [
        collapse_ws("".join(k.itertext()))
        for k in citation.findall("KeywordList/Keyword")
    ]
    publication_types = [
        _text(t, ".") for t in article_el.findall("PublicationTypeList/PublicationType")
    ]
    venue_type = "journal"
    if any("Review" in t for t in publication_types):
        venue_type = "review"
    if any("Preprint" in t for t in publication_types):
        venue_type = "preprint"

    return Paper(
        title=title,
        abstract=" ".join(abstract_parts),
        authors=authors,
        year=year,
        venue=_text(article_el, "Journal/Title") or _text(article_el, "Journal/ISOAbbreviation"),
        venue_type=venue_type,
        doi=doi,
        pmid=pmid,
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
        pdf_url=(
            f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc_id}/pdf/" if pmc_id else ""
        ),
        is_open_access=bool(pmc_id),
        fields_of_study=[t for t in mesh_terms if t][:8],
        keywords=[k for k in ([*keywords, *mesh_terms]) if k][:10],
        language=_text(article_el, "Language").lower()[:2],
        raw={"pubmed": {"pmc": pmc_id, "publication_types": publication_types,
                        "mesh": mesh_terms[:20]}},
    )


class PubMedProvider(Provider):
    meta = ProviderMeta(
        id="pubmed",
        name="PubMed",
        name_zh="PubMed 生物医学",
        description="Biomedical and life-sciences literature with curated MeSH "
                    "terms. Structured abstracts, PMC full text when open.",
        description_zh="生物医学与生命科学文献，含人工标注 MeSH 主题词与结构化摘要。",
        homepage="https://pubmed.ncbi.nlm.nih.gov",
        docs_url="https://www.ncbi.nlm.nih.gov/books/NBK25501/",
        tier="freemium",
        coverage="~37M citations",
        disciplines=["medicine", "biology", "life sciences", "public health"],
        requires_key=False,
        key_setting="ncbi",
        signup_url="https://www.ncbi.nlm.nih.gov/account/settings/",
    )
    capabilities = ProviderCapabilities(
        full_text_search=True,
        field_search=True,
        boolean_operators=True,
        year_range=True,
        author_filter=True,
        sort_by_date=True,
        returns_abstract=True,
        max_results_per_request=200,
        supports_pagination=True,
    )
    # 3 req/s keyless. 0.4s interval with 1 concurrent request stays safely
    # under that even with retries in flight.
    rate_limit = RateLimit(
        min_interval_s=0.4, max_concurrency=1, max_queries=2,
        note="NCBI: 3 req/s without a key, 10 req/s with one",
    )

    def _auth(self) -> dict[str, str]:
        key = self.api_key()
        params = {"tool": "PaperCreator"}
        from ...core.config import get_settings

        email = get_settings().identity.contact_email.strip()
        if email:
            params["email"] = email
        if key:
            params["api_key"] = key
        return params

    def _build_term(self, request: SearchRequest, query_text: str) -> str:
        """PubMed query syntax: ``term[Field]`` joined by AND/OR/NOT."""
        clauses = [query_text.strip()] if query_text.strip() else []
        for author in request.authors[:3]:
            clauses.append(f"{author}[Author]")
        if request.year_from or request.year_to:
            start = request.year_from or 1800
            end = request.year_to or 3000
            clauses.append(f'("{start}"[Date - Publication] : "{end}"[Date - Publication])')
        if request.open_access_only:
            clauses.append('"open access"[Filter]')
        for excluded in request.exclude_keywords[:3]:
            clauses.append(f"NOT {excluded}")
        term = " AND ".join(c for c in clauses if not c.startswith("NOT"))
        for clause in (c for c in clauses if c.startswith("NOT")):
            term = f"{term} {clause}"
        return term or "review[Publication Type]"

    async def search(self, request: SearchRequest, limit: int) -> list[Paper]:
        queries = request.effective_queries() or [request.query]
        if request.mode in ("idea", "paper") and request.seed_text:
            # PubMed has no semantic search and rejects very long terms; rely on
            # expanded keyword queries, else use a truncated seed.
            if not request.expanded_queries:
                queries = [collapse_ws(request.seed_text)[:300]]

        pmids: list[str] = []
        for query_text in queries[: self.rate_limit.max_queries]:
            body = await self.client.get_json(
                self.id, _ESEARCH,
                params={
                    "db": "pubmed",
                    "term": self._build_term(request, query_text),
                    "retmode": "json",
                    "retmax": min(limit, self.capabilities.max_results_per_request),
                    "sort": "date" if request.sort == "date" else "relevance",
                    **self._auth(),
                },
                **self.rate_limit.request_kwargs(),
            )
            found = ((body or {}).get("esearchresult") or {}).get("idlist") or []
            for pmid in found:
                if pmid not in pmids:
                    pmids.append(pmid)
            if len(pmids) >= limit:
                break
        if not pmids:
            return []

        papers: list[Paper] = []
        # efetch accepts a comma-separated id list; 200 per call keeps the URL
        # and the response size manageable.
        for start in range(0, min(len(pmids), limit), 200):
            chunk = pmids[start: start + 200]
            xml_text = await self.client.get_text(
                self.id, _EFETCH,
                params={"db": "pubmed", "id": ",".join(chunk), "retmode": "xml",
                        **self._auth()},
                **self.rate_limit.request_kwargs(),
            )
            papers.extend(self._parse_fetch(xml_text))
        return papers[:limit]

    def _parse_fetch(self, xml_text: str) -> list[Paper]:
        try:
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as exc:
            raise ProviderError(
                f"PubMed returned malformed XML: {exc}",
                details={"snippet": xml_text[:300]},
            ) from exc
        out: list[Paper] = []
        for article in root.findall(".//PubmedArticle"):
            try:
                paper = _parse_article(article)
            except (AttributeError, ValueError, TypeError) as exc:
                log.debug("skipping unparseable PubMed article: %s", exc)
                continue
            if paper is not None:
                out.append(paper)
        return out

    async def fetch_by_id(self, external_id: str) -> Paper | None:
        pmid = external_id.strip()
        if not pmid.isdigit():
            return None
        xml_text = await self.client.get_text(
            self.id, _EFETCH,
            params={"db": "pubmed", "id": pmid, "retmode": "xml", **self._auth()},
            **self.rate_limit.request_kwargs(),
        )
        papers = self._parse_fetch(xml_text)
        return papers[0] if papers else None

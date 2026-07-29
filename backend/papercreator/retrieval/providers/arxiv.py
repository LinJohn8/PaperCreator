"""arXiv provider.

API: ``https://export.arxiv.org/api/query`` (Atom XML).
Docs: https://info.arxiv.org/help/api/user-manual.html

Notes that shaped this implementation:

* **Rate limit is a hard 1 request / 3 seconds** with a single connection, per
  arXiv's terms of use. That is slow, so the provider batches aggressively
  (``max_results`` up to 2000 in one call) rather than paginating.
* **Query syntax is strict.** Bare multi-word input is implicitly OR-ed across
  terms, which produces junk: ``all:graph neural network`` matched papers about
  random networks in the live test. Multi-word phrases must be quoted, so
  :func:`build_query` quotes them explicitly.
* No citation counts, no venue for preprints. Abstracts are full-text quality,
  which makes arXiv the best free source for embedding-based analysis in CS/ML.
"""

from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree

from ...core.errors import ProviderError
from ...core.logging_setup import get_logger
from ...core.models import Author, Paper, SearchRequest
from ...core.util import collapse_ws, normalize_arxiv_id
from ..base import Provider, ProviderCapabilities, ProviderMeta, RateLimit

log = get_logger(__name__)

_ENDPOINT = "https://export.arxiv.org/api/query"
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}

# arXiv category -> readable field, for fields_of_study.
_CATEGORY_NAMES = {
    "cs.AI": "Artificial Intelligence", "cs.LG": "Machine Learning",
    "cs.CL": "Computation and Language", "cs.CV": "Computer Vision",
    "cs.NE": "Neural and Evolutionary Computing", "cs.RO": "Robotics",
    "cs.IR": "Information Retrieval", "cs.CR": "Cryptography and Security",
    "cs.DB": "Databases", "cs.DC": "Distributed Computing",
    "cs.HC": "Human-Computer Interaction", "cs.SE": "Software Engineering",
    "stat.ML": "Machine Learning (Statistics)", "eess.IV": "Image and Video Processing",
    "eess.SP": "Signal Processing", "math.OC": "Optimization and Control",
    "q-bio.QM": "Quantitative Methods", "physics.comp-ph": "Computational Physics",
}

_PHRASE_SPLIT = re.compile(r'"([^"]+)"|(\S+)')

# Words that carry no retrieval signal but, AND-ed in, would exclude good hits.
_QUERY_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "for", "with", "and", "or", "to", "from",
    "by", "at", "as", "is", "are", "be", "using", "use", "based", "via", "that",
    "this", "these", "those", "we", "our", "their", "its", "it",
}


def _term_expression(text: str) -> str:
    """Turn free text into an arXiv field expression.

    arXiv OR-es bare words, so ``all:graph neural network`` matches papers about
    *any* of those words - verified live, it returned "Random Neural Networks"
    for a molecular-property query. Wrapping the whole string in quotes is the
    opposite failure: a 5-word phrase matches literally nothing.

    So: quote genuine multi-word phrases the caller marked with quotes, and
    AND together the remaining significant words. Verified against the live API
    to return sensible results for long queries.
    """
    cleaned = collapse_ws(text)
    if not cleaned:
        return ""
    # Preserve explicit boolean input from advanced users untouched.
    if re.search(r"\b(AND|OR|ANDNOT)\b", cleaned):
        return cleaned

    parts: list[str] = []
    if '"' in cleaned:
        for quoted, bare in _PHRASE_SPLIT.findall(cleaned):
            if quoted:
                parts.append(f'all:"{quoted}"')
            elif bare.lower() not in _QUERY_STOPWORDS and len(bare) > 1:
                parts.append(f"all:{bare}")
        return " AND ".join(parts)

    words = [w for w in re.split(r"[^\w\-]+", cleaned) if w]
    significant = [w for w in words if w.lower() not in _QUERY_STOPWORDS and len(w) > 1]
    if not significant:
        significant = words
    if len(significant) == 1:
        return f"all:{significant[0]}"
    # A two-word query is almost always a compound term ("attention mechanism"),
    # where the phrase is more precise than the conjunction.
    if len(significant) == 2:
        return f'all:"{" ".join(significant)}"'
    # Longer: AND the words. Capped because arXiv's parser degrades on very
    # long expressions and each additional term narrows results sharply.
    return " AND ".join(f"all:{w}" for w in significant[:8])


def build_query(request: SearchRequest, query_text: str) -> str:
    """Compose the ``search_query`` parameter.

    Field prefixes: ``all:``, ``ti:``, ``abs:``, ``au:``, ``cat:``.
    Year filtering uses ``submittedDate:[YYYYMMDDTTTT TO YYYYMMDDTTTT]``.
    """
    clauses: list[str] = []
    core = _term_expression(query_text)
    if core:
        clauses.append(core)
    for author in request.authors[:3]:
        clauses.append(f'au:"{author}"')
    for field in request.fields_of_study[:3]:
        # Treat an exact category id as a category filter, else a topical term.
        if re.match(r"^[a-z-]+\.[A-Za-z-]+$", field):
            clauses.append(f"cat:{field}")
        else:
            clauses.append(f'all:"{field}"')
    if request.year_from or request.year_to:
        start = f"{request.year_from or 1991}01010000"
        end = f"{(request.year_to or 2100)}12312359"
        clauses.append(f"submittedDate:[{start} TO {end}]")
    return " AND ".join(clauses) if clauses else "all:*"


def _parse_entry(entry: ElementTree.Element) -> Paper | None:
    title_el = entry.find("atom:title", _NS)
    if title_el is None or not (title_el.text or "").strip():
        return None
    raw_id = (entry.findtext("atom:id", "", _NS) or "").strip()
    arxiv_id = normalize_arxiv_id(raw_id)

    authors: list[Author] = []
    for author_el in entry.findall("atom:author", _NS):
        name = (author_el.findtext("atom:name", "", _NS) or "").strip()
        affiliation = (author_el.findtext("arxiv:affiliation", "", _NS) or "").strip()
        if name:
            authors.append(Author(name=name, affiliation=affiliation))

    published = (entry.findtext("atom:published", "", _NS) or "").strip()
    year = None
    if len(published) >= 4 and published[:4].isdigit():
        year = int(published[:4])

    pdf_url = ""
    html_url = ""
    for link in entry.findall("atom:link", _NS):
        href = link.get("href") or ""
        if link.get("title") == "pdf" or link.get("type") == "application/pdf":
            pdf_url = href
        elif link.get("rel") == "alternate":
            html_url = href
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

    categories = [
        c.get("term", "") for c in entry.findall("atom:category", _NS) if c.get("term")
    ]
    primary = entry.find("arxiv:primary_category", _NS)
    if primary is not None and primary.get("term"):
        term = primary.get("term", "")
        categories = [term, *[c for c in categories if c != term]]

    doi = (entry.findtext("arxiv:doi", "", _NS) or "").strip()
    journal_ref = (entry.findtext("arxiv:journal_ref", "", _NS) or "").strip()
    comment = (entry.findtext("arxiv:comment", "", _NS) or "").strip()

    return Paper(
        title=collapse_ws(title_el.text or ""),
        abstract=collapse_ws(entry.findtext("atom:summary", "", _NS) or ""),
        authors=authors,
        year=year,
        venue=journal_ref or "arXiv",
        venue_type="journal" if journal_ref else "preprint",
        doi=doi,
        arxiv_id=arxiv_id,
        url=html_url or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
        pdf_url=pdf_url,
        is_open_access=True,  # every arXiv record is freely readable
        fields_of_study=[_CATEGORY_NAMES.get(c, c) for c in categories[:6]],
        keywords=categories[:6],
        raw={"arxiv": {"categories": categories, "comment": comment,
                       "journal_ref": journal_ref, "published": published}},
    )


class ArxivProvider(Provider):
    meta = ProviderMeta(
        id="arxiv",
        name="arXiv",
        name_zh="arXiv 预印本",
        description="Open-access preprints in physics, maths, CS, biology, "
                    "economics. Full abstracts, no citation counts.",
        description_zh="物理、数学、计算机、生物、经济领域的开放预印本，摘要完整，无引用数。",
        homepage="https://arxiv.org",
        docs_url="https://info.arxiv.org/help/api/user-manual.html",
        tier="free",
        coverage="~2.4M preprints",
        disciplines=["computer science", "physics", "mathematics", "statistics",
                     "quantitative biology", "economics"],
    )
    capabilities = ProviderCapabilities(
        full_text_search=True,
        field_search=True,
        boolean_operators=True,
        year_range=True,
        author_filter=True,
        fields_of_study_filter=True,
        sort_by_date=True,
        returns_abstract=True,
        returns_pdf_url=True,
        max_results_per_request=2000,
        supports_pagination=True,
    )
    # arXiv's terms: one request every three seconds, single connection.
    rate_limit = RateLimit(
        min_interval_s=3.1, max_concurrency=1, max_retries=2, max_queries=2,
        note="arXiv terms of use: 1 request / 3s, single connection",
    )

    async def search(self, request: SearchRequest, limit: int) -> list[Paper]:
        queries = request.effective_queries() or [request.query]
        if request.mode in ("idea", "paper") and request.seed_text:
            # arXiv has no semantic search; a long abstract as a phrase query
            # matches nothing. The pipeline supplies keyword variants in
            # expanded_queries; fall back to the first sentence if it did not.
            if not request.expanded_queries:
                queries = [collapse_ws(request.seed_text.split(".")[0])[:200]]

        collected: dict[str, Paper] = {}
        # One request per query variant, each costing 3s - cap the count.
        for query_text in queries[: self.rate_limit.max_queries]:
            search_query = build_query(request, query_text)
            sort_by = "submittedDate" if request.sort == "date" else "relevance"
            params = {
                "search_query": search_query,
                "start": 0,
                "max_results": min(limit, self.capabilities.max_results_per_request),
                "sortBy": sort_by,
                "sortOrder": "descending",
            }
            text = await self.client.get_text(
                self.id, _ENDPOINT, params=params,
                **self.rate_limit.request_kwargs(),
            )
            for paper in self._parse_feed(text):
                key = paper.arxiv_id or paper.title.lower()
                if key not in collected:
                    collected[key] = paper
            if len(collected) >= limit:
                break
        return list(collected.values())[:limit]

    def _parse_feed(self, text: str) -> list[Paper]:
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError as exc:
            raise ProviderError(
                f"arXiv returned malformed XML: {exc}",
                details={"snippet": text[:300]},
            ) from exc
        papers: list[Paper] = []
        for entry in root.findall("atom:entry", _NS):
            try:
                paper = _parse_entry(entry)
            except (AttributeError, ValueError, TypeError) as exc:
                log.debug("skipping unparseable arXiv entry: %s", exc)
                continue
            if paper is not None:
                papers.append(paper)
        return papers

    async def fetch_by_id(self, external_id: str) -> Paper | None:
        arxiv_id = normalize_arxiv_id(external_id)
        if not arxiv_id:
            return None
        text = await self.client.get_text(
            self.id, _ENDPOINT, params={"id_list": arxiv_id, "max_results": 1},
            **self.rate_limit.request_kwargs(),
        )
        papers = self._parse_feed(text)
        return papers[0] if papers else None

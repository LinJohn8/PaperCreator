"""Cross-provider result ranking.

Each provider returns its own ranked list with incomparable scores (arXiv gives
none, OpenAlex a relevance_score, S2 an internal order). Merging them needs a
method that uses only *rank*, not score. That is Reciprocal Rank Fusion:

    RRF(d) = sum over providers of  1 / (k + rank_provider(d))

with k=60, the value from the original Cormack et al. paper and the standard
default in Elasticsearch and Vespa. RRF is used because it is
scale-free, robust to one provider producing outliers, and naturally rewards
documents that several independent sources rank highly - which for literature
search is a strong relevance signal.

On top of fusion, :func:`rank_papers` applies transparent, individually
switchable boosts (recency, citations, abstract presence, open access, seed-term
overlap). Every component is reported in ``paper.raw["ranking"]`` so a
surprising order can always be explained rather than being a black box.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

from ..core.logging_setup import get_logger
from ..core.models import Paper, SearchRequest
from ..core.util import clamp, utc_now

log = get_logger(__name__)

RRF_K = 60.0

# Relative influence of each signal on the final score. Fusion dominates; the
# rest break ties and nudge. Tuned so that a paper appearing in 3 providers
# always outranks a single-provider hit of similar quality.
WEIGHTS = {
    "fusion": 1.0,
    "providers": 0.25,
    "citations": 0.20,
    "recency": 0.15,
    "abstract": 0.10,
    "open_access": 0.05,
    "term_overlap": 0.30,
}


def _tokenize(text: str) -> set[str]:
    return {t for t in re.split(r"\W+", (text or "").lower()) if len(t) > 2}


def _citation_score(citations: int) -> float:
    """Log-compressed citation count in 0..1.

    Raw counts span five orders of magnitude, so a linear term would let one
    classic paper dominate every ranking. log1p/log1p(10000) maps 0->0,
    10->0.25, 100->0.5, 1000->0.75, 10000->1.
    """
    return clamp(math.log1p(max(0, citations)) / math.log1p(10000), 0.0, 1.0)


def _recency_score(year: int | None, *, half_life_years: float = 6.0) -> float:
    """Exponential decay from the current year.

    Half-life of six years: a paper from six years ago scores 0.5, twelve years
    0.25. Recency is a mild preference, not a filter - foundational work must
    still surface.
    """
    if not year:
        return 0.3  # unknown year: neutral-ish, slightly penalised
    age = max(0, utc_now().year - year)
    return clamp(0.5 ** (age / half_life_years), 0.0, 1.0)


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[str]], k: float = RRF_K
) -> dict[str, float]:
    """RRF over ``{provider_id: [paper_id in rank order]}``.

    Returns raw (unnormalised) fusion scores keyed by paper id.
    """
    scores: dict[str, float] = defaultdict(float)
    for paper_ids in ranked_lists.values():
        for index, paper_id in enumerate(paper_ids):
            scores[paper_id] += 1.0 / (k + index + 1)
    return dict(scores)


def rank_papers(
    papers: list[Paper],
    *,
    provider_lists: dict[str, list[str]] | None = None,
    request: SearchRequest | None = None,
    weights: dict[str, float] | None = None,
) -> list[Paper]:
    """Score and sort merged results. Mutates ``paper.score`` and returns sorted.

    ``provider_lists`` is the per-provider ranking captured *before* dedupe. If
    omitted, fusion contributes nothing and the quality signals decide.
    """
    if not papers:
        return []
    active = {**WEIGHTS, **(weights or {})}

    fusion_raw = reciprocal_rank_fusion(provider_lists or {})
    max_fusion = max(fusion_raw.values(), default=0.0) or 1.0
    max_providers = max((len(p.source_providers) for p in papers), default=1) or 1

    seed_tokens: set[str] = set()
    if request is not None:
        seed_tokens = _tokenize(request.query) | _tokenize(request.seed_text)
        for expanded in request.expanded_queries:
            seed_tokens |= _tokenize(expanded)

    for paper in papers:
        components: dict[str, float] = {
            "fusion": (fusion_raw.get(paper.id, 0.0) / max_fusion),
            "providers": len(paper.source_providers) / max_providers,
            "citations": _citation_score(paper.citation_count),
            "recency": _recency_score(paper.year),
            "abstract": 1.0 if len(paper.abstract) > 200 else (
                0.5 if paper.abstract else 0.0
            ),
            "open_access": 1.0 if paper.is_open_access else 0.0,
            "term_overlap": 0.0,
        }
        if seed_tokens:
            haystack = _tokenize(f"{paper.title} {paper.abstract} "
                                 f"{' '.join(paper.keywords)}")
            overlap = len(seed_tokens & haystack) / len(seed_tokens)
            title_overlap = len(seed_tokens & _tokenize(paper.title)) / len(seed_tokens)
            components["term_overlap"] = clamp(overlap + title_overlap, 0.0, 1.0)

        total_weight = sum(active.get(k, 0.0) for k in components) or 1.0
        score = sum(components[k] * active.get(k, 0.0) for k in components) / total_weight
        paper.score = round(clamp(score, 0.0, 1.0), 5)
        # Keep the breakdown so the UI can show "why is this first?".
        paper.raw.setdefault("ranking", {}).update(
            {k: round(v, 4) for k, v in components.items()}
        )

    reverse_sort = True
    if request is not None and request.sort == "date":
        papers.sort(key=lambda p: (p.year or 0, p.score), reverse=reverse_sort)
    elif request is not None and request.sort == "citations":
        papers.sort(key=lambda p: (p.citation_count, p.score), reverse=reverse_sort)
    else:
        papers.sort(key=lambda p: p.score, reverse=reverse_sort)
    return papers


def apply_post_filters(
    papers: list[Paper], request: SearchRequest
) -> tuple[list[Paper], dict[str, int]]:
    """Enforce filters that some providers could not apply server-side.

    Returns the surviving papers and a count of what each filter removed, which
    the UI reports so an empty result set is explainable.
    """
    removed = {"year": 0, "open_access": 0, "excluded_keyword": 0, "venue": 0,
               "no_title": 0}
    kept: list[Paper] = []
    excluded = [k.lower() for k in request.exclude_keywords if k.strip()]
    venue_terms = [v.lower() for v in request.venues if v.strip()]

    for paper in papers:
        if not paper.title.strip():
            removed["no_title"] += 1
            continue
        if request.year_from and (paper.year or 0) < request.year_from:
            removed["year"] += 1
            continue
        if request.year_to and (paper.year or 9999) > request.year_to:
            removed["year"] += 1
            continue
        if request.open_access_only and not paper.is_open_access:
            removed["open_access"] += 1
            continue
        if excluded:
            haystack = f"{paper.title} {paper.abstract}".lower()
            if any(term in haystack for term in excluded):
                removed["excluded_keyword"] += 1
                continue
        if venue_terms:
            venue = paper.venue.lower()
            if venue and not any(term in venue for term in venue_terms):
                removed["venue"] += 1
                continue
        kept.append(paper)
    return kept, {k: v for k, v in removed.items() if v}


def diversify(papers: list[Paper], *, max_per_venue: int = 0) -> list[Paper]:
    """Optionally cap results per venue to avoid a single-journal result page.

    Off by default (``max_per_venue=0``) because for a literature review the
    user usually wants completeness, not variety.
    """
    if max_per_venue <= 0:
        return papers
    counts: dict[str, int] = defaultdict(int)
    out, deferred = [], []
    for paper in papers:
        key = paper.venue.lower() or "unknown"
        if counts[key] < max_per_venue:
            counts[key] += 1
            out.append(paper)
        else:
            deferred.append(paper)
    return [*out, *deferred]

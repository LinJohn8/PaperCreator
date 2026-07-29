"""Cross-provider deduplication.

Searching eight sources for one topic returns the same paper up to eight times,
in different shapes: arXiv has the abstract, OpenAlex the citations, Crossref the
page numbers, DBLP the real conference name. Deduplication has to *merge* those
into one enriched record, not just discard extras.

Algorithm
---------
1. **Identifier blocking.** Group by normalised DOI, then arXiv id, then
   PMID/OpenAlex/S2 id. These are exact and cheap, and catch most duplicates.
2. **Title blocking.** Remaining records are bucketed by a coarse key (first
   four alphanumeric-normalised title characters + year, plus a year-less
   bucket) so the expensive comparison only runs within small groups.
   Comparing every pair would be O(n²) on 300+ records.
3. **Similarity confirmation.** Within a bucket, candidates merge when
   normalised title similarity >= threshold *and* the year is compatible
   (equal, missing, or off by one - preprint vs published version).
4. **Merge.** ``store.papers.merge_papers`` decides field by field.

Deliberate conservatism: a false merge silently loses a distinct paper, which is
worse for a literature review than a duplicate the user can spot. Hence the
0.90 default threshold and the author-surname guard.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..core.logging_setup import get_logger
from ..core.models import Paper
from ..core.util import normalize_title, title_similarity
from ..store.papers import merge_papers

log = get_logger(__name__)


def _identifier_keys(paper: Paper) -> list[str]:
    """Strong identity keys, most reliable first."""
    keys = []
    if paper.doi:
        keys.append(f"doi:{paper.doi}")
    if paper.arxiv_id:
        keys.append(f"arxiv:{paper.arxiv_id}")
    if paper.pmid:
        keys.append(f"pmid:{paper.pmid}")
    if paper.openalex_id:
        keys.append(f"oa:{paper.openalex_id}")
    if paper.s2_id:
        keys.append(f"s2:{paper.s2_id}")
    return keys


def _title_buckets(paper: Paper) -> list[str]:
    """Coarse blocking keys for title comparison.

    Both a year-qualified and a year-free bucket are emitted so a preprint
    (year 2022) and its journal version (2023) still land in a shared bucket.
    """
    normalised = normalize_title(paper.title)
    if len(normalised) < 8:
        return []
    prefix = normalised[:4]
    return [f"t:{prefix}:{paper.year or ''}", f"t:{prefix}"]


def _surnames(paper: Paper) -> set[str]:
    out = set()
    for author in paper.authors[:6]:
        parts = author.name.replace(".", " ").split()
        if parts:
            out.add(parts[-1].lower())
    return out


def _compatible_years(a: Paper, b: Paper) -> bool:
    """Years match, one is missing, or they differ by one.

    One year of slack covers the preprint/publication gap and provider
    disagreement about online-first dates.
    """
    if a.year is None or b.year is None:
        return True
    return abs(a.year - b.year) <= 1


def _authors_compatible(a: Paper, b: Paper) -> bool:
    """Reject a title-similarity match when author sets clearly disagree.

    Guards against merging distinct papers that share a generic title
    ("Introduction to Machine Learning"). Missing author data is not evidence of
    disagreement, so it passes.
    """
    sa, sb = _surnames(a), _surnames(b)
    if not sa or not sb:
        return True
    return bool(sa & sb)


def _is_same_work(a: Paper, b: Paper, threshold: float) -> bool:
    """Decide whether two records describe the same work.

    Two tiers, because provider year data is not trustworthy enough to be a
    hard gate on its own:

    * **Exact title match** (normalised similarity 1.0) plus compatible authors
      is treated as conclusive and *ignores* the year. Observed live: OpenAlex
      carries "Attention Is All You Need" with publication year 2025 (a
      re-registration under DOI 10.65215/2q58a426) while every other source says
      2017. Requiring year agreement there loses a genuine duplicate, and two
      byte-identical normalised titles with a shared author are effectively
      never distinct papers.
    * **Fuzzy title match** (>= ``threshold``) additionally requires year
      compatibility, because near-matches are where real false positives live
      ("... Part I" vs "... Part II", conference vs extended journal version of
      a differently-scoped paper).
    """
    similarity = title_similarity(a.title, b.title)
    if similarity < threshold:
        return False
    if not _authors_compatible(a, b):
        return False
    if similarity >= 0.999:
        return True
    return _compatible_years(a, b)


def deduplicate(
    papers: list[Paper], *, title_threshold: float = 0.90
) -> tuple[list[Paper], int, dict[str, Any]]:
    """Merge duplicates. Returns ``(unique, merged_count, report)``.

    Input order is preserved for the survivors, which keeps provider ranking
    meaningful for the subsequent fusion step.
    """
    if not papers:
        return [], 0, {"by_identifier": 0, "by_title": 0, "groups": 0}

    # Canonical record per group, keyed by an arbitrary group id.
    groups: dict[int, Paper] = {}
    order: list[int] = []
    identifier_index: dict[str, int] = {}
    bucket_index: dict[str, list[int]] = defaultdict(list)

    by_identifier = by_title = 0

    for paper in papers:
        paper.ensure_id()
        target: int | None = None

        for key in _identifier_keys(paper):
            if key in identifier_index:
                target = identifier_index[key]
                by_identifier += 1
                break

        if target is None:
            best_score = 0.0
            seen_candidates: set[int] = set()
            for bucket in _title_buckets(paper):
                for candidate_id in bucket_index.get(bucket, ()):
                    if candidate_id in seen_candidates:
                        continue
                    seen_candidates.add(candidate_id)
                    candidate = groups[candidate_id]
                    if not _is_same_work(paper, candidate, title_threshold):
                        continue
                    score = title_similarity(paper.title, candidate.title)
                    if score > best_score:
                        best_score = score
                        target = candidate_id
            if target is not None:
                by_title += 1

        if target is None:
            group_id = len(groups)
            groups[group_id] = paper
            order.append(group_id)
        else:
            groups[target] = merge_papers(groups[target], paper)
            group_id = target

        # Index the (possibly merged) record under all of its keys so later
        # records can find it by any identifier any provider supplied.
        current = groups[group_id]
        for key in _identifier_keys(current):
            identifier_index.setdefault(key, group_id)
        for bucket in _title_buckets(current):
            if group_id not in bucket_index[bucket]:
                bucket_index[bucket].append(group_id)

    unique = [groups[gid] for gid in order]
    merged = len(papers) - len(unique)
    report = {
        "input": len(papers),
        "output": len(unique),
        "merged": merged,
        "by_identifier": by_identifier,
        "by_title": by_title,
        "groups": len(unique),
    }
    if merged:
        log.info(
            "deduplicated %s -> %s records (%s by id, %s by title)",
            len(papers), len(unique), by_identifier, by_title,
        )
    return unique, merged, report


def find_duplicates_in_library(
    papers: list[Paper], *, title_threshold: float = 0.92
) -> list[list[str]]:
    """Report suspected duplicate groups without merging anything.

    Powers the library's "find duplicates" maintenance action, where the user
    decides. Threshold is stricter than the automatic path because a
    user-visible false positive is annoying.
    """
    buckets: dict[str, list[Paper]] = defaultdict(list)
    for paper in papers:
        for bucket in _title_buckets(paper):
            buckets[bucket].append(paper)

    seen_pairs: set[tuple[str, str]] = set()
    clusters: dict[str, set[str]] = {}
    for candidates in buckets.values():
        for i, left in enumerate(candidates):
            for right in candidates[i + 1:]:
                if left.id == right.id:
                    continue
                pair = tuple(sorted((left.id, right.id)))  # type: ignore[assignment]
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)  # type: ignore[arg-type]
                if not _is_same_work(left, right, title_threshold):
                    continue
                # Union the two into a cluster.
                merged_set = clusters.get(left.id, {left.id}) | clusters.get(
                    right.id, {right.id}
                )
                for member in merged_set:
                    clusters[member] = merged_set
    unique_clusters: list[list[str]] = []
    emitted: set[frozenset[str]] = set()
    for members in clusters.values():
        key = frozenset(members)
        if len(members) > 1 and key not in emitted:
            emitted.add(key)
            unique_clusters.append(sorted(members))
    return unique_clusters

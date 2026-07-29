"""Citation and co-authorship graph analysis.

Complements the embedding landscape with the *relational* view: the embedding map
says which papers are about similar things, the citation graph says which papers
actually build on each other. Both are needed - two papers can be topically
adjacent and never cite one another, which is precisely the cluster-bridge gap.

Built from ``paper.references_ids``, which currently only OpenAlex populates, so
coverage is reported explicitly and every consumer checks it rather than silently
drawing an empty graph.

Implemented on plain dicts and numpy rather than networkx: the operations needed
here (degree, PageRank, co-citation, components) are a few dozen lines each, and
adding a graph library for that is not worth another dependency.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

import numpy as np

from ..core.logging_setup import get_logger
from ..core.models import Paper

log = get_logger(__name__)


def build_citation_graph(papers: list[Paper]) -> dict[str, Any]:
    """Directed citation graph restricted to the given paper set.

    External references (to papers not in the set) are counted but not added as
    nodes - a graph where 90% of nodes are stubs is unreadable and the interesting
    structure is internal.
    """
    index_by_external: dict[str, str] = {}
    for paper in papers:
        for key in (paper.openalex_id, paper.doi, paper.arxiv_id, paper.pmid):
            if key:
                index_by_external[str(key).lower()] = paper.id

    edges: list[tuple[str, str]] = []
    out_degree: dict[str, int] = defaultdict(int)
    in_degree: dict[str, int] = defaultdict(int)
    external_refs = 0
    papers_with_refs = 0

    for paper in papers:
        if paper.references_ids:
            papers_with_refs += 1
        for reference in paper.references_ids:
            target = index_by_external.get(str(reference).lower())
            if target is None:
                external_refs += 1
                continue
            if target == paper.id:
                continue
            edges.append((paper.id, target))
            out_degree[paper.id] += 1
            in_degree[target] += 1

    coverage = papers_with_refs / len(papers) if papers else 0.0
    if coverage < 0.2 and papers:
        log.info(
            "citation graph coverage is low (%.0f%% of papers have reference "
            "data); only OpenAlex supplies reference lists",
            coverage * 100,
        )
    return {
        "nodes": [p.id for p in papers],
        "edges": edges,
        "in_degree": dict(in_degree),
        "out_degree": dict(out_degree),
        "internal_edges": len(edges),
        "external_references": external_refs,
        "papers_with_reference_data": papers_with_refs,
        "coverage": round(coverage, 4),
    }


def pagerank(
    nodes: list[str],
    edges: list[tuple[str, str]],
    *,
    damping: float = 0.85,
    iterations: int = 60,
    tolerance: float = 1e-7,
) -> dict[str, float]:
    """PageRank over the citation graph.

    Edges point *citing -> cited*, so rank flows to work that is built upon,
    which is the usual notion of influence within a corpus. Dangling nodes (no
    outgoing internal citations) redistribute uniformly, the standard treatment.
    """
    if not nodes:
        return {}
    n = len(nodes)
    position = {node: i for i, node in enumerate(nodes)}
    outgoing: list[list[int]] = [[] for _ in range(n)]
    for source, target in edges:
        if source in position and target in position:
            outgoing[position[source]].append(position[target])

    rank = np.full(n, 1.0 / n, dtype=np.float64)
    for _ in range(iterations):
        contribution = np.zeros(n, dtype=np.float64)
        dangling_mass = 0.0
        for i in range(n):
            targets = outgoing[i]
            if not targets:
                dangling_mass += rank[i]
                continue
            share = rank[i] / len(targets)
            for target in targets:
                contribution[target] += share
        updated = (
            (1.0 - damping) / n
            + damping * (contribution + dangling_mass / n)
        )
        if float(np.abs(updated - rank).sum()) < tolerance:
            rank = updated
            break
        rank = updated
    return {node: round(float(rank[i]), 8) for node, i in position.items()}


def co_citation_pairs(
    papers: list[Paper], *, min_shared: int = 2, limit: int = 200
) -> list[dict[str, Any]]:
    """Papers frequently cited *together* by the same citing papers.

    Co-citation is a classic similarity signal that is independent of text: two
    papers repeatedly cited side by side are treated as related by the field
    itself, even if their abstracts share no vocabulary. Useful as a check on the
    embedding map.
    """
    cited_by: dict[str, set[str]] = defaultdict(set)
    id_by_external: dict[str, str] = {}
    for paper in papers:
        for key in (paper.openalex_id, paper.doi, paper.arxiv_id):
            if key:
                id_by_external[str(key).lower()] = paper.id
    for paper in papers:
        for reference in paper.references_ids:
            target = id_by_external.get(str(reference).lower())
            if target:
                cited_by[target].add(paper.id)

    counts: dict[tuple[str, str], int] = defaultdict(int)
    ids = sorted(cited_by)
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            shared = len(cited_by[left] & cited_by[right])
            if shared >= min_shared:
                counts[(left, right)] = shared
    ranked = sorted(counts.items(), key=lambda item: -item[1])[:limit]
    return [
        {"source": pair[0], "target": pair[1], "shared_citers": count}
        for pair, count in ranked
    ]


def bibliographic_coupling(
    papers: list[Paper], *, min_shared: int = 3, limit: int = 200
) -> list[dict[str, Any]]:
    """Papers that cite many of the same references.

    The mirror image of co-citation: coupling is visible immediately on
    publication (it depends on the paper's own bibliography), so it links recent
    work that has not accumulated citations yet - useful for placing new preprints.
    """
    reference_sets = {
        paper.id: {str(r).lower() for r in paper.references_ids}
        for paper in papers if paper.references_ids
    }
    ids = sorted(reference_sets)
    scored: list[tuple[tuple[str, str], int, float]] = []
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            shared = len(reference_sets[left] & reference_sets[right])
            if shared < min_shared:
                continue
            union = len(reference_sets[left] | reference_sets[right]) or 1
            scored.append(((left, right), shared, shared / union))
    scored.sort(key=lambda item: (-item[1], -item[2]))
    return [
        {"source": pair[0], "target": pair[1], "shared_references": shared,
         "jaccard": round(jaccard, 4)}
        for pair, shared, jaccard in scored[:limit]
    ]


def connected_components(
    nodes: list[str], edges: list[tuple[str, str]]
) -> list[list[str]]:
    """Weakly connected components, largest first.

    A corpus that splits into several disconnected components is a strong signal
    of separate research communities - and the boundaries between them are where
    bridge gaps live.
    """
    adjacency: dict[str, set[str]] = {node: set() for node in nodes}
    for source, target in edges:
        if source in adjacency and target in adjacency:
            adjacency[source].add(target)
            adjacency[target].add(source)
    seen: set[str] = set()
    components: list[list[str]] = []
    for node in nodes:
        if node in seen:
            continue
        queue = deque([node])
        component: list[str] = []
        seen.add(node)
        while queue:
            current = queue.popleft()
            component.append(current)
            for neighbour in adjacency[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        components.append(sorted(component))
    components.sort(key=len, reverse=True)
    return components


def coauthor_graph(papers: list[Paper], *, min_papers: int = 2) -> dict[str, Any]:
    """Co-authorship network, keyed by normalised author name.

    Author disambiguation is a hard problem and this does not solve it: names are
    normalised (lowercased, initials collapsed) and that is all. Two distinct
    researchers sharing a name will be merged. Stated here because the resulting
    numbers should be read as indicative, not authoritative.
    """
    def normalise(name: str) -> str:
        parts = [p for p in name.replace(".", " ").lower().split() if p]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return f"{parts[0][0]} {parts[-1]}"

    paper_count: dict[str, int] = defaultdict(int)
    display: dict[str, str] = {}
    pairs: dict[tuple[str, str], int] = defaultdict(int)
    papers_by_author: dict[str, list[str]] = defaultdict(list)

    for paper in papers:
        names = []
        for author in paper.authors:
            key = normalise(author.name)
            if not key:
                continue
            names.append(key)
            paper_count[key] += 1
            display.setdefault(key, author.name)
            papers_by_author[key].append(paper.id)
        unique_names = sorted(set(names))
        for i, left in enumerate(unique_names):
            for right in unique_names[i + 1:]:
                pairs[(left, right)] += 1

    authors = [
        {
            "id": key,
            "name": display.get(key, key),
            "papers": count,
            "paper_ids": papers_by_author[key][:20],
        }
        for key, count in paper_count.items() if count >= min_papers
    ]
    authors.sort(key=lambda a: -a["papers"])
    active = {a["id"] for a in authors}
    edges = [
        {"source": left, "target": right, "weight": weight}
        for (left, right), weight in pairs.items()
        if left in active and right in active
    ]
    edges.sort(key=lambda e: -e["weight"])
    return {
        "authors": authors[:200],
        "edges": edges[:500],
        "total_authors": len(paper_count),
        "note": "names are normalised to 'first-initial lastname'; distinct "
                "researchers with the same name are merged",
    }


def analyse_graph(papers: list[Paper]) -> dict[str, Any]:
    """Full relational analysis for the graph view."""
    graph = build_citation_graph(papers)
    by_id = {p.id: p for p in papers}
    ranks = pagerank(graph["nodes"], graph["edges"])
    components = connected_components(graph["nodes"], graph["edges"])

    influential = sorted(
        (
            {
                "paper_id": pid,
                "title": by_id[pid].title if pid in by_id else "",
                "year": by_id[pid].year if pid in by_id else None,
                "pagerank": rank,
                "cited_by_in_set": graph["in_degree"].get(pid, 0),
                "cites_in_set": graph["out_degree"].get(pid, 0),
                "global_citations": by_id[pid].citation_count if pid in by_id else 0,
            }
            for pid, rank in ranks.items()
        ),
        key=lambda item: (-item["pagerank"], -item["cited_by_in_set"]),
    )[:20]

    return {
        "citation": {
            "internal_edges": graph["internal_edges"],
            "external_references": graph["external_references"],
            "coverage": graph["coverage"],
            "papers_with_reference_data": graph["papers_with_reference_data"],
            "edges": [
                {"source": s, "target": t} for s, t in graph["edges"][:1500]
            ],
            "components": [
                {"size": len(c), "paper_ids": c[:50]} for c in components[:10]
            ],
            "isolated_papers": sum(1 for c in components if len(c) == 1),
        },
        "influential_papers": influential,
        "co_citation": co_citation_pairs(papers),
        "bibliographic_coupling": bibliographic_coupling(papers),
        "coauthors": coauthor_graph(papers),
        "caveats": [
            "Reference lists come only from OpenAlex; coverage is reported above "
            "and low coverage makes citation metrics unreliable.",
            "Only citations between papers in this set are counted - external "
            "citation counts are shown separately as 'global_citations'.",
        ],
    }

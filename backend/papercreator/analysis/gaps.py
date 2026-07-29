"""Research gap detection.

This is the module behind "so you can see where the gaps are". It must be
described honestly: these are **heuristics over retrieved metadata**, not proof
that nobody has done the work. A region can look empty because:

* nobody has published there (a real gap),
* the retrieval did not cover it (a search gap),
* the work exists under vocabulary the embedding placed elsewhere,
* or the projection folded distinct topics onto each other.

So every candidate carries its ``evidence`` and the papers that bound it, and the
UI presents them as *questions to investigate*. Five complementary detectors run,
because each catches a different shape of absence:

1. :func:`detect_sparse_regions` - low-density pockets *inside* the convex hull of
   the literature. Interpolation, not extrapolation: a hole surrounded by work is
   interesting; empty space beyond the frontier is usually just unrelated.
2. :func:`detect_cluster_bridges` - pairs of clusters that are close in embedding
   space yet share almost no papers between them. The classic "these two
   communities should be talking" gap.
3. :func:`detect_temporal_stale` - regions that were active and then went quiet.
   Either abandoned for good reason, or ripe for revisiting with new methods.
4. :func:`detect_underexplored_pairs` - keyword pairs that co-occur far less than
   their individual frequencies predict.
5. :func:`detect_frontier` - the sparse outer edge, reported separately and scored
   lower, because "nobody works out here" is weaker evidence than an interior hole.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from ..core.logging_setup import get_logger
from ..core.models import ClusterInfo, GapCandidate, Paper
from ..core.util import new_id, utc_now
from .heatmap import compute_grid, density_at_points, scott_bandwidth

log = get_logger(__name__)


def _inside_hull(candidates: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Boolean mask: which candidate positions lie inside the data's hull.

    Uses scipy's Delaunay when available; otherwise falls back to a k-nearest
    surround test (a point is "inside" when its neighbours are distributed on
    more than one side), which is approximate but adequate and dependency-free.
    """
    if candidates.size == 0 or points.shape[0] < 4:
        return np.zeros(len(candidates), dtype=bool)
    try:
        from scipy.spatial import Delaunay

        hull = Delaunay(points[:, :2])
        return hull.find_simplex(candidates[:, :2]) >= 0
    except Exception:  # noqa: BLE001 - scipy optional or degenerate hull
        pass

    # Fallback: require neighbours in at least 3 of the 4 quadrants around the
    # candidate, which excludes positions hanging off the edge of the cloud.
    mask = np.zeros(len(candidates), dtype=bool)
    for index, candidate in enumerate(candidates):
        offsets = points[:, :2] - candidate[:2]
        distances = np.linalg.norm(offsets, axis=1)
        nearest = np.argsort(distances)[:12]
        quadrants = set()
        for offset in offsets[nearest]:
            quadrants.add((offset[0] >= 0, offset[1] >= 0))
        mask[index] = len(quadrants) >= 3
    return mask


def detect_sparse_regions(
    points: np.ndarray,
    papers: list[Paper],
    *,
    grid_size: int = 40,
    min_score: float = 0.35,
    max_candidates: int = 8,
    keywords_by_cluster: dict[int, list[str]] | None = None,
    labels: np.ndarray | None = None,
) -> list[GapCandidate]:
    """Low-density pockets surrounded by literature."""
    if points.shape[0] < 12:
        return []
    grid, extent, bandwidth = compute_grid(points, grid_size=grid_size)
    grid_max = float(grid.max()) or 1.0
    normalised = grid / grid_max

    xs = np.linspace(extent[0], extent[1], grid_size)
    ys = np.linspace(extent[2], extent[3], grid_size)

    # Candidate cells: low density, but not the absolute void (a cell with zero
    # neighbours nearby is outside the field, handled by detect_frontier).
    flat = normalised.ravel()
    low_threshold = float(np.percentile(flat[flat > 0], 25)) if (flat > 0).any() else 0.0
    candidate_cells: list[tuple[float, int, int]] = []
    for yi in range(1, grid_size - 1):
        for xi in range(1, grid_size - 1):
            value = float(normalised[yi, xi])
            if value > low_threshold:
                continue
            # Contrast with the surrounding ring: a genuine pocket is much
            # emptier than its immediate neighbourhood.
            ring = normalised[yi - 1: yi + 2, xi - 1: xi + 2]
            surround = float(ring.mean())
            if surround <= 0:
                continue
            contrast = (surround - value) / surround
            if contrast > 0.05:
                candidate_cells.append((value, yi, xi))
    if not candidate_cells:
        return []

    candidate_positions = np.array(
        [[xs[xi], ys[yi], float(np.median(points[:, 2]))] for _, yi, xi in candidate_cells],
        dtype=np.float32,
    )
    inside = _inside_hull(candidate_positions, points)
    interior = [
        (candidate_cells[i], candidate_positions[i])
        for i in range(len(candidate_cells)) if inside[i]
    ]
    if not interior:
        return []

    # Merge adjacent cells into regions so one hole yields one candidate.
    merged: list[dict[str, Any]] = []
    used: set[tuple[int, int]] = set()
    cell_lookup = {(yi, xi): (value, position)
                   for (value, yi, xi), position in interior}
    for (yi, xi), (value, position) in sorted(
        cell_lookup.items(), key=lambda item: item[1][0]
    ):
        if (yi, xi) in used:
            continue
        stack = [(yi, xi)]
        members: list[tuple[int, int]] = []
        while stack:
            cell = stack.pop()
            if cell in used or cell not in cell_lookup:
                continue
            used.add(cell)
            members.append(cell)
            cy, cx = cell
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                neighbour = (cy + dy, cx + dx)
                if neighbour in cell_lookup and neighbour not in used:
                    stack.append(neighbour)
        positions = np.array([cell_lookup[c][1] for c in members], dtype=np.float32)
        merged.append({
            "centre": positions.mean(axis=0),
            "cells": len(members),
            "density": float(np.mean([cell_lookup[c][0] for c in members])),
            "radius": float(
                max(
                    (extent[1] - extent[0]) / grid_size * math.sqrt(len(members)),
                    bandwidth * 0.5,
                )
            ),
        })

    total_cells = max(1, grid_size * grid_size)
    scored_regions: list[tuple[float, dict[str, Any], np.ndarray]] = []
    for region in merged:
        centre = region["centre"]
        distances = np.linalg.norm(points[:, :2] - centre[:2], axis=1)
        nearest = np.argsort(distances)[:8]

        # A gap is a *pocket*: empty, surrounded by work, and of moderate size.
        emptiness = 1.0 - region["density"]
        surround_factor = min(1.0, 8.0 / max(1.0, float(distances[nearest].mean())))
        # Size preference peaks around 0.5% of the grid and falls away on both
        # sides. Too small is noise between two adjacent points; too large is a
        # projection artefact, not an opportunity. Observed live with PCA: the
        # corpus was pushed into one corner, leaving a 49-of-1600-cell void that
        # the hull test accepted as interior and that scored a perfect 1.000.
        area_share = region["cells"] / total_cells
        ideal_share = 0.005
        size_factor = math.exp(
            -((math.log((area_share + 1e-6) / ideal_share)) ** 2) / 2.0
        )
        score = round(
            0.45 * emptiness + 0.30 * size_factor + 0.25 * surround_factor, 4
        )
        if score < min_score:
            continue
        region["area_share"] = round(area_share, 5)
        region["size_factor"] = round(size_factor, 4)
        region["surround_factor"] = round(surround_factor, 4)
        scored_regions.append((score, region, nearest))

    # Sort by the final score, not by raw density: the size penalty reorders.
    scored_regions.sort(key=lambda item: -item[0])

    out: list[GapCandidate] = []
    for score, region, nearest in scored_regions[:max_candidates]:
        centre = region["centre"]
        distances = np.linalg.norm(points[:, :2] - centre[:2], axis=1)
        emptiness = 1.0 - region["density"]

        neighbour_clusters: list[int] = []
        keywords: list[str] = []
        if labels is not None:
            counts = Counter(int(labels[i]) for i in nearest if int(labels[i]) >= 0)
            neighbour_clusters = [label for label, _ in counts.most_common(3)]
            for label in neighbour_clusters:
                keywords.extend((keywords_by_cluster or {}).get(label, [])[:3])

        out.append(GapCandidate(
            id=new_id("gap"),
            kind="sparse_region",
            score=score,
            center=[round(float(v), 4) for v in centre[:3]],
            radius=round(region["radius"], 4),
            related_cluster_ids=neighbour_clusters,
            nearest_paper_ids=[papers[int(i)].id for i in nearest[:6]],
            keywords=list(dict.fromkeys(keywords))[:6],
            description=(
                f"Sparse region between {len(neighbour_clusters)} nearby topic "
                f"cluster(s), spanning {region['cells']} grid cell(s) at "
                f"{emptiness:.0%} below surrounding density. Nearest work: "
                f"{papers[int(nearest[0])].title[:70]}"
            ),
            description_zh=(
                f"位于 {len(neighbour_clusters)} 个相邻主题簇之间的稀疏区域，"
                f"覆盖 {region['cells']} 个网格单元，密度低于周边 {emptiness:.0%}。"
            ),
            evidence={
                "normalised_density": round(region["density"], 5),
                "grid_cells": region["cells"],
                "area_share_of_map": region.get("area_share"),
                "size_factor": region.get("size_factor"),
                "surround_factor": region.get("surround_factor"),
                "mean_distance_to_nearest_8": round(float(distances[nearest].mean()), 4),
                "detector": "kde_interior_pocket",
                "caveat": "low density in the retrieved set; verify with a targeted "
                          "search before treating it as unexplored",
            },
        ))
    return out


def detect_cluster_bridges(
    embeddings: np.ndarray,
    points: np.ndarray,
    papers: list[Paper],
    labels: np.ndarray,
    clusters: list[ClusterInfo],
    *,
    max_candidates: int = 6,
    min_score: float = 0.3,
) -> list[GapCandidate]:
    """Cluster pairs that are semantically close but barely connected.

    "Barely connected" is measured two ways: few papers sit between them in
    embedding space, and (when reference data is available) they rarely cite each
    other. Two adjacent-but-unlinked communities is the most actionable gap type,
    because the contribution is obvious: apply one side's method to the other's
    problem.
    """
    if len(clusters) < 2:
        return []
    by_id = {c.id: c for c in clusters}
    centroids: dict[int, np.ndarray] = {}
    for cluster in clusters:
        members = embeddings[labels == cluster.id]
        if members.size:
            centroid = members.mean(axis=0)
            centroids[cluster.id] = centroid / (np.linalg.norm(centroid) or 1.0)
    if len(centroids) < 2:
        return []

    # Cross-cluster citation counts, when providers supplied reference lists.
    external_to_index: dict[str, int] = {}
    for index, paper in enumerate(papers):
        for key in (paper.openalex_id, paper.doi, paper.arxiv_id):
            if key:
                external_to_index[str(key).lower()] = index
    cross_citations: dict[tuple[int, int], int] = defaultdict(int)
    for index, paper in enumerate(papers):
        source = int(labels[index])
        if source < 0:
            continue
        for reference in paper.references_ids:
            target_index = external_to_index.get(str(reference).lower())
            if target_index is None:
                continue
            target = int(labels[target_index])
            if target >= 0 and target != source:
                cross_citations[tuple(sorted((source, target)))] += 1  # type: ignore[index]

    ids = sorted(centroids)
    scored: list[tuple[float, dict[str, Any]]] = []
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            similarity = float(centroids[left] @ centroids[right])
            if similarity <= 0.15:
                continue  # genuinely unrelated topics; not a gap
            citations = cross_citations.get((left, right), 0)
            left_size = max(1, by_id[left].size or int((labels == left).sum()))
            right_size = max(1, by_id[right].size or int((labels == right).sum()))
            # Expected citation traffic scales with the smaller cluster.
            expected = max(1.0, 0.15 * min(left_size, right_size))
            isolation = 1.0 - min(1.0, citations / expected)

            # Papers lying between the two centroids in embedding space.
            midpoint = (centroids[left] + centroids[right]) / 2.0
            midpoint = midpoint / (np.linalg.norm(midpoint) or 1.0)
            between = int((embeddings @ midpoint > similarity + 0.05).sum())
            sparsity = 1.0 - min(1.0, between / max(4.0, 0.1 * len(papers)))

            score = round(0.4 * similarity + 0.35 * isolation + 0.25 * sparsity, 4)
            if score < min_score:
                continue
            scored.append((score, {
                "left": left, "right": right, "similarity": round(similarity, 4),
                "citations": citations, "between": between,
                "isolation": round(isolation, 4), "sparsity": round(sparsity, 4),
            }))

    scored.sort(key=lambda pair: -pair[0])
    out: list[GapCandidate] = []
    for score, info in scored[:max_candidates]:
        left_cluster, right_cluster = by_id[info["left"]], by_id[info["right"]]
        left_points = points[labels == info["left"]]
        right_points = points[labels == info["right"]]
        centre = (
            (left_points.mean(axis=0) + right_points.mean(axis=0)) / 2.0
            if left_points.size and right_points.size
            else np.zeros(3, dtype=np.float32)
        )
        shared_keywords = set(left_cluster.keywords[:8]) & set(right_cluster.keywords[:8])
        out.append(GapCandidate(
            id=new_id("gap"),
            kind="cluster_bridge",
            score=score,
            center=[round(float(v), 4) for v in centre[:3]],
            radius=round(float(np.linalg.norm(
                left_points.mean(axis=0)[:2] - right_points.mean(axis=0)[:2]
            ) / 3.0) if left_points.size and right_points.size else 1.0, 4),
            related_cluster_ids=[info["left"], info["right"]],
            nearest_paper_ids=[
                *left_cluster.representative_paper_ids[:2],
                *right_cluster.representative_paper_ids[:2],
            ],
            keywords=[
                *left_cluster.keywords[:3], *right_cluster.keywords[:3],
            ],
            description=(
                f"'{left_cluster.label}' and '{right_cluster.label}' are "
                f"semantically close (cosine {info['similarity']:.2f}) but only "
                f"{info['citations']} citation link(s) and {info['between']} "
                f"paper(s) sit between them"
                + (f"; they share the term(s) {', '.join(sorted(shared_keywords))}"
                   if shared_keywords else "")
            ),
            description_zh=(
                f"「{left_cluster.label}」与「{right_cluster.label}」语义相近"
                f"（余弦 {info['similarity']:.2f}），但两者之间仅有 "
                f"{info['citations']} 条引用关联、{info['between']} 篇中间论文。"
            ),
            evidence={
                **info,
                "left_size": left_cluster.size,
                "right_size": right_cluster.size,
                "detector": "centroid_similarity_vs_citation_traffic",
                "caveat": "citation counts depend on providers returning reference "
                          "lists; absence of links may reflect missing metadata",
            },
        ))
    return out


def detect_temporal_stale(
    points: np.ndarray,
    papers: list[Paper],
    labels: np.ndarray,
    clusters: list[ClusterInfo],
    *,
    stale_years: int = 4,
    max_candidates: int = 5,
) -> list[GapCandidate]:
    """Clusters that were active and then stopped."""
    current_year = utc_now().year
    out: list[GapCandidate] = []
    for cluster in clusters:
        indices = np.flatnonzero(labels == cluster.id)
        years = sorted(
            [papers[int(i)].year for i in indices if papers[int(i)].year]
        )
        if len(years) < 4:
            continue
        last_year = years[-1]
        silence = current_year - last_year
        if silence < stale_years:
            continue
        # Only interesting if it was genuinely active before going quiet.
        span = years[-1] - years[0]
        if span < 2:
            continue
        peak_rate = len(years) / max(1, span)
        score = round(min(1.0, 0.15 * silence) * 0.6 + min(1.0, peak_rate / 4) * 0.4, 4)
        cluster_points = points[indices]
        out.append(GapCandidate(
            id=new_id("gap"),
            kind="temporal_stale",
            score=score,
            center=[round(float(v), 4) for v in cluster_points.mean(axis=0)[:3]],
            radius=round(float(np.std(cluster_points[:, :2])) or 1.0, 4),
            related_cluster_ids=[cluster.id],
            nearest_paper_ids=cluster.representative_paper_ids[:4],
            keywords=cluster.keywords[:6],
            description=(
                f"'{cluster.label}' ({cluster.size} papers) has no work newer than "
                f"{last_year} - {silence} years of silence after activity from "
                f"{years[0]} to {last_year}. Either settled, or open to revisiting "
                f"with current methods."
            ),
            description_zh=(
                f"「{cluster.label}」（{cluster.size} 篇）自 {last_year} 年后无新工作，"
                f"已沉寂 {silence} 年；此前从 {years[0]} 到 {last_year} 持续活跃。"
            ),
            evidence={
                "last_year": last_year,
                "first_year": years[0],
                "years_silent": silence,
                "papers_per_year_when_active": round(peak_rate, 2),
                "cluster_size": cluster.size,
                "detector": "cluster_recency",
                "caveat": "reflects the retrieved set only; recent work may exist "
                          "outside the queried sources or date filter",
            },
        ))
    out.sort(key=lambda gap: -gap.score)
    return out[:max_candidates]


def detect_underexplored_pairs(
    papers: list[Paper],
    labels: np.ndarray,
    points: np.ndarray,
    *,
    top_terms: int = 25,
    max_candidates: int = 6,
    min_score: float = 0.3,
) -> list[GapCandidate]:
    """Keyword pairs that co-occur much less than chance would predict.

    Under independence, ``P(a and b) = P(a) * P(b)``. A pair whose observed
    co-occurrence is far below that, while both terms are individually common, is
    a combination the field has not tried - the textual analogue of a
    cluster bridge, and often easier for the user to act on because it names two
    concrete concepts.
    """
    from .keywords import paper_terms

    n_papers = len(papers)
    if n_papers < 15:
        return []
    term_sets = [set(paper_terms(paper, weight_curated=1)) for paper in papers]
    frequency: Counter[str] = Counter()
    for terms in term_sets:
        frequency.update(terms)
    # Restrict to reasonably common multi-word terms: single words are too
    # ambiguous to make a meaningful pair claim.
    common = [
        term for term, count in frequency.most_common(top_terms * 4)
        if count >= max(3, n_papers * 0.05) and " " in term
    ][:top_terms]
    if len(common) < 2:
        return []

    co_occurrence: dict[tuple[str, str], int] = defaultdict(int)
    for terms in term_sets:
        present = [t for t in common if t in terms]
        for i, left in enumerate(present):
            for right in present[i + 1:]:
                co_occurrence[(left, right)] += 1

    scored: list[tuple[float, dict[str, Any]]] = []
    for i, left in enumerate(common):
        for right in common[i + 1:]:
            if left in right or right in left:
                continue
            observed = co_occurrence.get((left, right), 0)
            expected = frequency[left] * frequency[right] / n_papers
            if expected < 1.0:
                continue
            deficit = 1.0 - min(1.0, observed / expected)
            if deficit < 0.5:
                continue
            prevalence = min(
                1.0, (frequency[left] + frequency[right]) / (2.0 * n_papers) * 4
            )
            score = round(0.65 * deficit + 0.35 * prevalence, 4)
            if score < min_score:
                continue
            scored.append((score, {
                "left": left, "right": right, "observed": observed,
                "expected": round(expected, 2), "deficit": round(deficit, 4),
                "left_count": frequency[left], "right_count": frequency[right],
            }))

    scored.sort(key=lambda pair: -pair[0])
    out: list[GapCandidate] = []
    for score, info in scored[:max_candidates]:
        # Place the marker between the two terms' centroids on the map.
        left_indices = [i for i, terms in enumerate(term_sets) if info["left"] in terms]
        right_indices = [i for i, terms in enumerate(term_sets) if info["right"] in terms]
        centre = np.zeros(3, dtype=np.float32)
        if left_indices and right_indices:
            centre = (
                points[np.asarray(left_indices)].mean(axis=0)
                + points[np.asarray(right_indices)].mean(axis=0)
            ) / 2.0
        out.append(GapCandidate(
            id=new_id("gap"),
            kind="underexplored_pair",
            score=score,
            center=[round(float(v), 4) for v in centre[:3]],
            radius=1.5,
            related_cluster_ids=sorted({
                int(labels[i]) for i in (left_indices + right_indices)[:20]
                if int(labels[i]) >= 0
            })[:3],
            nearest_paper_ids=[
                papers[i].id for i in (left_indices[:2] + right_indices[:2])
            ],
            keywords=[info["left"], info["right"]],
            description=(
                f"'{info['left']}' ({info['left_count']} papers) and "
                f"'{info['right']}' ({info['right_count']} papers) co-occur in only "
                f"{info['observed']} paper(s), against {info['expected']} expected "
                f"if independent - a {info['deficit']:.0%} shortfall"
            ),
            description_zh=(
                f"「{info['left']}」（{info['left_count']} 篇）与「{info['right']}」"
                f"（{info['right_count']} 篇）仅在 {info['observed']} 篇论文中同时出现，"
                f"独立假设下应有 {info['expected']} 篇，缺口 {info['deficit']:.0%}。"
            ),
            evidence={
                **info,
                "total_papers": n_papers,
                "detector": "pointwise_cooccurrence_deficit",
                "caveat": "term-level statistic; the combination may be described "
                          "with different vocabulary in existing work",
            },
        ))
    return out


def detect_frontier(
    points: np.ndarray,
    papers: list[Paper],
    *,
    max_candidates: int = 3,
) -> list[GapCandidate]:
    """The sparse outer edge of the map.

    Scored lower than interior gaps on purpose: being outside the field's current
    boundary is much weaker evidence of opportunity than a hole inside it.
    """
    if points.shape[0] < 20:
        return []
    densities = density_at_points(points)
    if densities.size == 0:
        return []
    centre = points[:, :2].mean(axis=0)
    radii = np.linalg.norm(points[:, :2] - centre, axis=1)
    # Outermost decile, ordered by how isolated each point is.
    edge_threshold = float(np.percentile(radii, 90))
    edge_indices = np.flatnonzero(radii >= edge_threshold)
    if edge_indices.size == 0:
        return []
    ranked = sorted(edge_indices.tolist(), key=lambda i: densities[i])

    out: list[GapCandidate] = []
    used_positions: list[np.ndarray] = []
    for index in ranked:
        position = points[index]
        # Spread the candidates out rather than reporting one neighbourhood.
        if any(
            float(np.linalg.norm(position[:2] - other[:2])) < 4.0
            for other in used_positions
        ):
            continue
        used_positions.append(position)
        density_percentile = float((densities < densities[index]).mean())
        score = round(0.35 * (1.0 - density_percentile) + 0.15, 4)
        out.append(GapCandidate(
            id=new_id("gap"),
            kind="low_density_frontier",
            score=score,
            center=[round(float(v), 4) for v in position[:3]],
            radius=2.0,
            related_cluster_ids=[],
            nearest_paper_ids=[papers[index].id],
            keywords=papers[index].keywords[:4],
            description=(
                f"Isolated frontier position near '{papers[index].title[:60]}' - "
                f"local density is in the bottom {density_percentile:.0%}. Weak "
                f"signal: sparse edges are often just unrelated territory."
            ),
            description_zh=(
                f"位于外缘的孤立位置，邻近论文「{papers[index].title[:40]}」，"
                f"局部密度处于最低 {density_percentile:.0%}。该信号较弱。"
            ),
            evidence={
                "local_density": round(float(densities[index]), 6),
                "density_percentile": round(density_percentile, 4),
                "radius_from_centre": round(float(radii[index]), 4),
                "detector": "frontier_isolation",
                "caveat": "weakest detector; edge sparsity usually means the region "
                          "is outside the field rather than an opportunity",
            },
        ))
        if len(out) >= max_candidates:
            break
    return out


def detect_all(
    *,
    embeddings: np.ndarray,
    points: np.ndarray,
    papers: list[Paper],
    labels: np.ndarray,
    clusters: list[ClusterInfo],
    keywords_by_cluster: dict[int, list[str]] | None = None,
    min_score: float = 0.35,
    grid_size: int = 40,
) -> list[GapCandidate]:
    """Run every detector and return one ranked, de-overlapped list.

    Individual detectors can fail on degenerate input (a corpus with no years, no
    reference data, one cluster). Each is isolated so one failure does not cost
    the user the others.
    """
    gaps: list[GapCandidate] = []
    detectors = (
        ("sparse_regions", lambda: detect_sparse_regions(
            points, papers, grid_size=grid_size, min_score=min_score,
            keywords_by_cluster=keywords_by_cluster, labels=labels,
        )),
        ("cluster_bridges", lambda: detect_cluster_bridges(
            embeddings, points, papers, labels, clusters, min_score=min_score * 0.85,
        )),
        ("temporal_stale", lambda: detect_temporal_stale(
            points, papers, labels, clusters,
        )),
        ("underexplored_pairs", lambda: detect_underexplored_pairs(
            papers, labels, points, min_score=min_score * 0.85,
        )),
        ("frontier", lambda: detect_frontier(points, papers)),
    )
    for name, detector in detectors:
        try:
            found = detector()
            gaps.extend(found)
            log.info("gap detector %s produced %s candidate(s)", name, len(found))
        except Exception as exc:  # noqa: BLE001 - one detector must not kill the rest
            log.warning("gap detector %s failed: %s", name, exc)

    gaps.sort(key=lambda gap: -gap.score)
    return _drop_overlapping(gaps)


def _drop_overlapping(
    gaps: list[GapCandidate], *, min_separation: float = 2.5
) -> list[GapCandidate]:
    """Keep the highest-scoring gap when several point at the same place.

    Different detectors legitimately find the same hole from different angles;
    showing the user three markers on one spot is noise. Types are kept separate
    because a bridge and a sparse region at the same location say different
    things.
    """
    kept: list[GapCandidate] = []
    for gap in gaps:
        if not gap.center:
            kept.append(gap)
            continue
        centre = np.asarray(gap.center[:2], dtype=np.float32)
        clash = False
        for existing in kept:
            if existing.kind != gap.kind or not existing.center:
                continue
            other = np.asarray(existing.center[:2], dtype=np.float32)
            if float(np.linalg.norm(centre - other)) < min_separation:
                clash = True
                break
        if not clash:
            kept.append(gap)
    return kept


def describe_detectors() -> list[dict[str, Any]]:
    """Detector catalogue, so the UI can explain and toggle each one."""
    return [
        {
            "id": "sparse_region",
            "name": "Sparse interior region",
            "name_zh": "内部稀疏区域",
            "strength": "medium-high",
            "needs": "12+ papers",
            "explains": "a low-density pocket surrounded by existing work",
        },
        {
            "id": "cluster_bridge",
            "name": "Unconnected neighbouring topics",
            "name_zh": "相邻但未连接的主题",
            "strength": "high",
            "needs": "2+ clusters; better with reference lists (OpenAlex)",
            "explains": "two close communities that barely cite each other",
        },
        {
            "id": "temporal_stale",
            "name": "Dormant topic",
            "name_zh": "沉寂主题",
            "strength": "medium",
            "needs": "publication years",
            "explains": "a cluster that was active then went quiet",
        },
        {
            "id": "underexplored_pair",
            "name": "Untried concept pair",
            "name_zh": "未尝试的概念组合",
            "strength": "medium",
            "needs": "15+ papers",
            "explains": "two common terms that rarely appear together",
        },
        {
            "id": "low_density_frontier",
            "name": "Frontier isolation",
            "name_zh": "边缘孤立区",
            "strength": "low",
            "needs": "20+ papers",
            "explains": "the sparse outer edge of the retrieved field",
        },
    ]

"""Placing new papers and ideas into an existing landscape.

This implements the requirement "I can add my own idea/paper and see *where* it
sits, and remove it again". The hard constraint: the existing points must not
move. If adding one idea re-ran the whole pipeline, every coordinate would shift
and the user's mental map would be destroyed - so the fitted reducer saved with
the analysis is reused to transform only the new point.

Three placement paths, in order of fidelity:

1. **Exact transform.** The stored reducer supports out-of-sample projection
   (UMAP, PCA) and the embedding space is portable. The new point is embedded,
   transformed, and the analysis's stored normalisation re-applied.
2. **Neighbour interpolation.** No usable transform (t-SNE/MDS, or a lost pickle
   after a library upgrade). The new paper's position is the similarity-weighted
   centroid of its nearest neighbours' *existing* coordinates. Approximate, but
   consistent with the map that is on screen, and clearly reported as such.
3. **Refusal with a reason.** The embedding space is corpus-relative (TF-IDF): a
   vector computed alone is not comparable to the fitted corpus, so placement
   would be meaningless. Fixed hashing remains available as a weaker but fully
   offline portable alternative.

The interpretation attached to the result (nearest cluster, local density,
novelty) is the part the user actually reads, so it is computed from the same
numbers shown on the map rather than from a separate model.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..core.errors import ConflictError, NotFoundError, ValidationError
from ..core.logging_setup import get_logger
from ..core.models import AnalysisResult, Paper, PaperPoint, PositionResult
from ..store import analyses as analyses_store
from ..store import papers as papers_store
from . import embeddings as embeddings_module
from . import heatmap as heatmap_module
from . import reduce as reduce_module

log = get_logger(__name__)


def _existing_coords(analysis: AnalysisResult) -> tuple[np.ndarray, list[str]]:
    coords = np.array(
        [[p.x, p.y, p.z] for p in analysis.points], dtype=np.float32
    )
    return coords, [p.paper_id for p in analysis.points]


def _load_existing_embeddings(
    paper_ids: list[str], model: str
) -> tuple[np.ndarray, list[int]]:
    """Cached vectors for the analysis members, with the indices that had one."""
    cached = analyses_store.get_embeddings_bulk(paper_ids, model)
    vectors: list[np.ndarray] = []
    indices: list[int] = []
    for index, paper_id in enumerate(paper_ids):
        entry = cached.get(paper_id)
        if entry is not None:
            vectors.append(np.frombuffer(entry[0], dtype=np.float32))
            indices.append(index)
    if not vectors:
        return np.zeros((0, 0), dtype=np.float32), []
    width = len(vectors[0])
    usable = [(v, i) for v, i in zip(vectors, indices) if len(v) == width]
    matrix = np.vstack([v for v, _ in usable]).astype(np.float32)
    return matrix, [i for _, i in usable]


def place_paper(
    analysis_id: str,
    paper: Paper,
    *,
    persist: bool = True,
    mark_as_seed: bool = True,
) -> PositionResult:
    """Project one paper into an existing landscape and interpret its position."""
    analysis = analyses_store.get_analysis(analysis_id)
    if analysis is None:
        raise NotFoundError(f"analysis {analysis_id} not found")
    if not analysis.points:
        raise ValidationError(f"analysis {analysis_id} has no points to place against")

    paper.ensure_id()
    existing_coords, existing_ids = _existing_coords(analysis)
    embedding_model = analysis.embedding_model

    if embedding_model.split(":")[0] in ("tfidf",):
        raise ConflictError(
            f"this landscape was built with corpus-relative '{embedding_model}' "
            "embeddings, so a new paper cannot be placed into it without "
            "refitting. Re-run the analysis with the paper included, or install "
            'the semantic stack: pip install "papercreator[analysis]"',
            details={
                "analysis_id": analysis_id,
                "embedding_model": embedding_model,
                "action": "rerun_analysis",
            },
        )

    # 1. Embed the new paper in the analysis's own space.
    backend_hint = analysis.config.embedding_backend or "auto"
    result = embeddings_module.embed_papers(
        [paper], backend=backend_hint, use_cache=True
    )
    if result.model != embedding_model:
        raise ConflictError(
            f"the configured embedding model is now '{result.model}' but this "
            f"landscape was built with '{embedding_model}'. Placing a vector from "
            "a different model would put it in an unrelated position - re-run the "
            "analysis to rebuild the map.",
            details={"analysis_id": analysis_id, "expected": embedding_model,
                     "actual": result.model, "action": "rerun_analysis"},
        )
    new_vector = result.vectors[0]

    # 2. Position it.
    projector = analyses_store.load_projector(analysis_id)
    method = "interpolated"
    coords: np.ndarray | None = None
    if isinstance(projector, dict) and projector.get("model") is not None:
        model = projector["model"]
        if hasattr(model, "transform"):
            try:
                raw = model.transform(new_vector.reshape(1, -1))
                coords = reduce_module.apply_normalisation(
                    np.asarray(raw, dtype=np.float32), projector.get("scaling") or {}
                ).ravel()
                method = "exact_transform"
            except Exception as exc:  # noqa: BLE001 - fall back, do not fail
                log.warning(
                    "stored %s projector could not transform the new point (%s); "
                    "using neighbour interpolation",
                    projector.get("reducer"), exc,
                )

    corpus_vectors, corpus_indices = _load_existing_embeddings(
        existing_ids, embedding_model
    )
    similarities = np.zeros(0, dtype=np.float32)
    if corpus_vectors.size and corpus_vectors.shape[1] == len(new_vector):
        similarities = np.clip(corpus_vectors @ new_vector, -1.0, 1.0)

    if coords is None:
        if similarities.size == 0:
            raise ConflictError(
                "cannot place this paper: the landscape has no cached embeddings "
                "to interpolate from and its reducer cannot transform new points. "
                "Re-run the analysis.",
                details={"analysis_id": analysis_id, "action": "rerun_analysis"},
            )
        coords = _interpolate_position(
            similarities, corpus_indices, existing_coords, k=8
        )

    if coords.size < 3:
        coords = np.pad(coords, (0, 3 - coords.size))

    # 3. Interpret the position.
    nearest_pairs = (
        sorted(
            zip(corpus_indices, similarities.tolist()), key=lambda pair: -pair[1]
        )[:8]
        if similarities.size else []
    )
    nearest_ids = [existing_ids[i] for i, _ in nearest_pairs]
    nearest_papers = papers_store.get_many(nearest_ids)
    by_id = {p.id: p for p in nearest_papers}

    cluster_by_paper = {p.paper_id: p.cluster for p in analysis.points}
    cluster_votes: dict[int, float] = {}
    for index, similarity in nearest_pairs[:5]:
        label = cluster_by_paper.get(existing_ids[index], -1)
        if label >= 0:
            cluster_votes[label] = cluster_votes.get(label, 0.0) + float(similarity)
    nearest_cluster = (
        max(cluster_votes, key=lambda k: cluster_votes[k]) if cluster_votes else -1
    )
    cluster_label = ""
    cluster_distance = 0.0
    for info in analysis.clusters:
        if info.id == nearest_cluster:
            cluster_label = info.label
            if info.centroid:
                cluster_distance = round(float(np.linalg.norm(
                    coords[:2] - np.asarray(info.centroid[:2], dtype=np.float32)
                )), 4)
            break

    all_densities = heatmap_module.density_at_points(existing_coords)
    local_density = float(
        heatmap_module.density_at_points(existing_coords, coords.reshape(1, -1))[0]
    )
    density_percentile = (
        float((all_densities < local_density).mean()) if all_densities.size else 0.0
    )
    # Novelty = how empty the neighbourhood is. Deliberately simple and stated as
    # such in the interpretation: it measures distance from *retrieved* work, not
    # originality in any absolute sense.
    novelty = round(1.0 - density_percentile, 4)

    nearest_gaps = []
    for gap in analysis.gaps:
        if not gap.center:
            continue
        distance = float(np.linalg.norm(
            coords[:2] - np.asarray(gap.center[:2], dtype=np.float32)
        ))
        nearest_gaps.append({
            "id": gap.id, "kind": gap.kind, "score": gap.score,
            "distance": round(distance, 4),
            "inside": distance <= max(gap.radius, 1.0),
            "description": gap.description,
            "keywords": gap.keywords,
        })
    nearest_gaps.sort(key=lambda g: g["distance"])
    nearest_gaps = nearest_gaps[:3]

    point = PaperPoint(
        paper_id=paper.id,
        x=round(float(coords[0]), 4),
        y=round(float(coords[1]), 4),
        z=round(float(coords[2]), 4),
        cluster=nearest_cluster,
        outlier=round(novelty, 4),
        is_seed=mark_as_seed,
        density=round(
            local_density / float(all_densities.max()) if all_densities.size
            and all_densities.max() else 0.0, 4
        ),
    )

    interpretation, interpretation_zh = _describe_position(
        paper=paper,
        method=method,
        cluster_label=cluster_label,
        nearest_pairs=nearest_pairs,
        existing_ids=existing_ids,
        by_id=by_id,
        novelty=novelty,
        density_percentile=density_percentile,
        nearest_gaps=nearest_gaps,
    )

    if persist:
        papers_store.upsert(paper)
        analyses_store.add_points(analysis_id, [point])

    return PositionResult(
        paper_id=paper.id,
        analysis_id=analysis_id,
        point=point,
        method=method,
        nearest_cluster=nearest_cluster,
        nearest_cluster_label=cluster_label,
        cluster_distance=cluster_distance,
        nearest_papers=[
            {
                "paper_id": existing_ids[index],
                "similarity": round(float(similarity), 4),
                "title": by_id[existing_ids[index]].title
                if existing_ids[index] in by_id else "",
                "year": by_id[existing_ids[index]].year
                if existing_ids[index] in by_id else None,
                "cluster": cluster_by_paper.get(existing_ids[index], -1),
            }
            for index, similarity in nearest_pairs
        ],
        local_density=round(local_density, 6),
        density_percentile=round(density_percentile, 4),
        novelty=novelty,
        nearest_gaps=nearest_gaps,
        interpretation=interpretation,
        interpretation_zh=interpretation_zh,
    )


def _interpolate_position(
    similarities: np.ndarray,
    corpus_indices: list[int],
    existing_coords: np.ndarray,
    *,
    k: int = 8,
) -> np.ndarray:
    """Similarity-weighted centroid of the k nearest neighbours' coordinates.

    Weights are ``max(0, similarity) ** 3``: cubing sharpens the weighting so the
    result sits near its closest match rather than drifting to the middle of a
    broad neighbourhood, which is the failure mode of linear weighting.
    """
    count = min(k, len(corpus_indices))
    order = np.argsort(-similarities)[:count]
    weights = np.clip(similarities[order], 0.0, None) ** 3
    if float(weights.sum()) <= 1e-9:
        weights = np.ones_like(weights)
    positions = existing_coords[[corpus_indices[i] for i in order]]
    return (positions * weights[:, None]).sum(axis=0) / weights.sum()


def _describe_position(
    *,
    paper: Paper,
    method: str,
    cluster_label: str,
    nearest_pairs: list[tuple[int, float]],
    existing_ids: list[str],
    by_id: dict[str, Paper],
    novelty: float,
    density_percentile: float,
    nearest_gaps: list[dict[str, Any]],
) -> tuple[str, str]:
    """Plain-language reading of the placement, in English and Chinese."""
    top_similarity = nearest_pairs[0][1] if nearest_pairs else 0.0
    top_title = ""
    if nearest_pairs:
        top_id = existing_ids[nearest_pairs[0][0]]
        top_title = by_id[top_id].title if top_id in by_id else ""

    if top_similarity >= 0.85:
        crowding = "very close to existing work"
        crowding_zh = "与已有工作高度接近"
    elif top_similarity >= 0.7:
        crowding = "within an established line of work"
        crowding_zh = "位于已有研究脉络之内"
    elif top_similarity >= 0.5:
        crowding = "adjacent to existing work but not covered by it"
        crowding_zh = "与已有工作相邻，但未被覆盖"
    else:
        crowding = "far from anything in the retrieved set"
        crowding_zh = "与检索到的文献均相距较远"

    parts = [
        f"Placed {'by exact projection' if method == 'exact_transform' else 'by neighbour interpolation'}"
        f" in {'cluster ' + cluster_label if cluster_label else 'no clear cluster'}.",
        f"It is {crowding}"
        + (f" (closest: '{top_title[:60]}', cosine {top_similarity:.2f})."
           if top_title else "."),
        f"Local density is at the {density_percentile:.0%} percentile of the map, "
        f"giving a novelty reading of {novelty:.2f}.",
    ]
    parts_zh = [
        f"通过{'精确投影' if method == 'exact_transform' else '邻域插值'}定位于"
        f"{'簇「' + cluster_label + '」' if cluster_label else '无明确簇'}。",
        f"该位置{crowding_zh}"
        + (f"（最相近：「{top_title[:40]}」，余弦 {top_similarity:.2f}）。"
           if top_title else "。"),
        f"局部密度处于地图的 {density_percentile:.0%} 分位，新颖度读数 {novelty:.2f}。",
    ]

    inside = [g for g in nearest_gaps if g["inside"]]
    if inside:
        gap = inside[0]
        parts.append(
            f"It falls inside a detected {gap['kind'].replace('_', ' ')} gap "
            f"(score {gap['score']:.2f}) - worth checking that gap's evidence."
        )
        parts_zh.append(
            f"该位置落在一个已识别的缺口内（类型 {gap['kind']}，评分 "
            f"{gap['score']:.2f}），建议查看该缺口的证据。"
        )
    elif nearest_gaps:
        gap = nearest_gaps[0]
        parts.append(
            f"Nearest detected gap is {gap['distance']:.1f} units away "
            f"({gap['kind'].replace('_', ' ')})."
        )
        parts_zh.append(
            f"最近的缺口距离 {gap['distance']:.1f}（类型 {gap['kind']}）。"
        )

    if method != "exact_transform":
        parts.append(
            "Note: this position is interpolated from neighbours because the "
            "landscape's reducer cannot project new points exactly."
        )
        parts_zh.append("注意：由于该地图的降维方法无法精确投影新点，此位置由邻域插值得出。")

    return " ".join(parts), "".join(parts_zh)


def place_idea(
    analysis_id: str,
    *,
    title: str,
    abstract: str,
    keywords: list[str] | None = None,
    project_id: str = "",
    persist: bool = True,
) -> PositionResult:
    """Place a free-text idea (not an existing paper) into a landscape.

    The idea is stored as a :class:`Paper` with ``origin='idea'`` so it lives in
    the library, can be re-placed into other analyses, cited, and deleted like
    anything else.
    """
    if not title.strip() and not abstract.strip():
        raise ValidationError("an idea needs at least a title or a description")
    paper = Paper(
        title=title.strip() or abstract.strip()[:120],
        abstract=abstract.strip(),
        keywords=keywords or [],
        origin="idea",
        source_providers=["user"],
    ).ensure_id()
    result = place_paper(analysis_id, paper, persist=persist, mark_as_seed=True)
    if persist and project_id:
        collection = papers_store.ensure_collection(project_id, "my ideas", kind="manual")
        papers_store.add_to_collection(collection["id"], [paper.id])
    return result


def remove_from_analysis(analysis_id: str, paper_ids: list[str]) -> dict[str, Any]:
    """Remove points from a landscape without recomputing it.

    The papers stay in the library; only their presence in this map is removed.
    Cluster assignments and gaps are left as computed - recomputing them would
    move existing points, which is the thing this module exists to avoid. The
    response says so, so the UI can offer a re-run.
    """
    analyses_store.require_analysis(analysis_id)
    removed = analyses_store.remove_points(analysis_id, paper_ids)
    return {
        "analysis_id": analysis_id,
        "removed": removed,
        "requested": len(paper_ids),
        "note": "clusters, keywords and gaps were not recomputed; re-run the "
                "analysis to refresh them",
    }

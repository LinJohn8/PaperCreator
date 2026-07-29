"""Analysis pipeline: papers -> a saved, renderable landscape.

Sequence::

    papers
      -> embed (cached)                     embeddings.embed_papers
      -> reduce to 3D (fitted, persisted)   reduce.reduce_vectors
      -> cluster in embedding space         cluster.cluster_vectors
      -> label clusters (c-TF-IDF)          keywords.cluster_keywords
      -> corpus keyword stats + trends      keywords.global_keyword_stats
      -> density grid + keyword layers      heatmap.build_heatmap
      -> gap candidates (5 detectors)       gaps.detect_all
      -> persist (points + fitted reducer)  store.analyses.save_analysis

Ordering rationale: clustering runs on the *embeddings*, not the 3D coordinates,
so groups reflect the literature rather than artefacts of the projection; but the
heatmap and gaps run on the *coordinates*, because that is the space the user is
looking at and pointing to.

Every stage degrades rather than fails. With none of the optional packages
installed the pipeline still produces a complete landscape (TF-IDF + PCA +
KMeans) and says so in ``warnings``.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from ..core.config import get_settings
from ..core.errors import ValidationError
from ..core.jobs import JobContext
from ..core.logging_setup import get_logger
from ..core.models import (
    AnalysisConfig,
    AnalysisResult,
    ClusterInfo,
    Paper,
    PaperPoint,
)
from ..core.util import utc_now_iso
from ..store import analyses as analyses_store
from ..store import papers as papers_store
from . import cluster, embeddings, gaps, heatmap, keywords, reduce

log = get_logger(__name__)

# Below this, clustering and gap detection are not meaningful; the map is still
# produced so the user sees their papers, but the extras are skipped.
MIN_PAPERS_FOR_FULL_ANALYSIS = 8


def config_from_settings(overrides: dict[str, Any] | None = None) -> AnalysisConfig:
    """Build a config from user settings, then apply per-request overrides."""
    settings = get_settings().analysis
    base = AnalysisConfig(
        embedding_backend=settings.embedding_backend,
        reducer=settings.reducer,
        clusterer=settings.clusterer,
        n_neighbors=settings.n_neighbors,
        min_dist=settings.min_dist,
        min_cluster_size=settings.min_cluster_size,
        keyword_top_k=settings.keyword_top_k,
        heatmap_grid=settings.heatmap_grid,
        random_state=settings.random_state,
    )
    if overrides:
        merged = {**base.model_dump(), **{k: v for k, v in overrides.items()
                                          if v is not None}}
        return AnalysisConfig.model_validate(merged)
    return base


def build_analysis(
    papers: list[Paper],
    *,
    config: AnalysisConfig | None = None,
    project_id: str = "",
    name: str = "",
    seed_paper_ids: set[str] | None = None,
    job: JobContext | None = None,
    persist: bool = True,
) -> AnalysisResult:
    """Run the full pipeline over ``papers``.

    ``seed_paper_ids`` marks the user's own idea/paper records so the UI can
    render them distinctly; they participate in the layout like any other point.
    """
    if not papers:
        raise ValidationError("cannot build an analysis from zero papers")
    started = time.perf_counter()
    cfg = config or config_from_settings()
    seeds = seed_paper_ids or {
        p.id for p in papers if p.origin in ("idea", "own_paper")
    }
    warnings: list[str] = []

    def progress(fraction: float, message: str) -> None:
        if job is not None:
            job.progress(fraction, message)
        log.info("analysis: %s", message)

    # ------------------------------------------------------------ 1. embed
    progress(0.05, f"embedding {len(papers)} papers")
    embedding_result = embeddings.embed_papers(
        papers,
        backend=cfg.embedding_backend,
        progress=lambda msg: progress(0.10, msg),
    )
    warnings.extend(embedding_result.warnings or [])
    vectors = embedding_result.vectors
    if vectors.size == 0 or vectors.shape[1] == 0:
        raise ValidationError(
            "embedding produced no usable vectors - the papers may all have "
            "empty titles and abstracts"
        )

    # ----------------------------------------------------------- 2. reduce
    progress(0.30, f"projecting to {cfg.dimensions}D with {cfg.reducer}")
    projection = reduce.reduce_vectors(
        vectors,
        dims=cfg.dimensions,
        reducer=cfg.reducer,
        n_neighbors=cfg.n_neighbors,
        min_dist=cfg.min_dist,
        metric=cfg.metric,
        random_state=cfg.random_state,
    )
    warnings.extend(projection.warnings)
    coords = projection.coords
    if coords.shape[1] < 3:
        coords = np.hstack([
            coords,
            np.zeros((coords.shape[0], 3 - coords.shape[1]), dtype=np.float32),
        ])

    # ---------------------------------------------------------- 3. cluster
    if len(papers) >= 4:
        progress(0.50, "clustering")
        assignment = cluster.cluster_vectors(
            vectors,
            method=cfg.clusterer,
            min_cluster_size=cfg.min_cluster_size,
            n_clusters=cfg.n_clusters,
        )
    else:
        assignment = cluster.ClusterAssignment(
            labels=np.zeros(len(papers), dtype=int), method="single", n_clusters=1,
            outlier_scores=np.zeros(len(papers), dtype=np.float32),
            warnings=[f"only {len(papers)} papers: no clustering performed"],
        )
    warnings.extend(assignment.warnings)
    labels = assignment.labels

    # --------------------------------------------------------- 4. keywords
    progress(0.62, "extracting cluster keywords")
    keywords_by_cluster = keywords.cluster_keywords(
        papers, labels, top_k=cfg.keyword_top_k
    )
    centroids = cluster.cluster_centroids(vectors, labels)
    coherence = cluster.cluster_coherence(vectors, labels, centroids)
    representatives = cluster.representative_papers(
        papers, vectors, labels, centroids
    )

    cluster_infos: list[ClusterInfo] = []
    for label in sorted({int(v) for v in labels if v >= 0}):
        member_indices = np.flatnonzero(labels == label)
        member_years = [
            papers[int(i)].year for i in member_indices if papers[int(i)].year
        ]
        member_citations = [papers[int(i)].citation_count for i in member_indices]
        cluster_keywords_list = keywords_by_cluster.get(label, [])
        centroid_coords = coords[member_indices].mean(axis=0)
        cluster_infos.append(ClusterInfo(
            id=label,
            label=keywords.label_cluster(cluster_keywords_list),
            size=int(member_indices.size),
            keywords=cluster_keywords_list,
            centroid=[round(float(v), 4) for v in centroid_coords[:3]],
            representative_paper_ids=representatives.get(label, []),
            year_min=min(member_years) if member_years else None,
            year_max=max(member_years) if member_years else None,
            year_median=(
                round(float(np.median(member_years)), 1) if member_years else None
            ),
            mean_citations=round(float(np.mean(member_citations)), 1)
            if member_citations else 0.0,
            coherence=coherence.get(label, 0.0),
        ))

    progress(0.70, "computing keyword statistics")
    keyword_stats = keywords.global_keyword_stats(
        papers, labels, top_k=60, min_papers=max(2, len(papers) // 50)
    )

    # ---------------------------------------------------------- 5. heatmap
    progress(0.78, "building density heatmap")
    layer_terms = [stat.term for stat in keyword_stats[:12]]
    keyword_indices = keywords.keyword_paper_map(papers, layer_terms)
    heat = heatmap.build_heatmap(
        coords,
        grid_size=cfg.heatmap_grid,
        bandwidth=cfg.heatmap_bandwidth,
        keyword_indices=keyword_indices,
    )
    point_densities = heatmap.density_at_points(coords)

    # ------------------------------------------------------------- 6. gaps
    gap_candidates = []
    if cfg.detect_gaps and len(papers) >= MIN_PAPERS_FOR_FULL_ANALYSIS:
        progress(0.86, "detecting research gaps")
        gap_candidates = gaps.detect_all(
            embeddings=vectors,
            points=coords,
            papers=papers,
            labels=labels,
            clusters=cluster_infos,
            keywords_by_cluster=keywords_by_cluster,
            min_score=cfg.gap_min_score,
            grid_size=cfg.heatmap_grid,
        )
    elif cfg.detect_gaps:
        warnings.append(
            f"gap detection needs at least {MIN_PAPERS_FOR_FULL_ANALYSIS} papers "
            f"(have {len(papers)})"
        )

    # ------------------------------------------------------------ assemble
    max_density = float(point_densities.max()) if point_densities.size else 1.0
    points_out = [
        PaperPoint(
            paper_id=paper.id,
            x=round(float(coords[index, 0]), 4),
            y=round(float(coords[index, 1]), 4),
            z=round(float(coords[index, 2]), 4),
            cluster=int(labels[index]),
            outlier=round(float(assignment.outlier_scores[index]), 4)
            if index < len(assignment.outlier_scores) else 0.0,
            is_seed=paper.id in seeds,
            density=round(
                float(point_densities[index] / max_density) if max_density else 0.0, 4
            ),
        )
        for index, paper in enumerate(papers)
    ]

    metrics: dict[str, Any] = {
        "embedding_backend": embedding_result.backend,
        "embedding_dim": embedding_result.dim,
        "embedding_cache_hits": embedding_result.cache_hits,
        "embedding_computed": embedding_result.computed,
        "corpus_relative_embeddings": embedding_result.corpus_relative,
        "reducer_supports_new_points": projection.supports_transform,
        "trustworthiness": reduce.trustworthiness_score(vectors, coords),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        **{f"reduce_{k}": v for k, v in projection.metrics.items()},
        **{f"cluster_{k}": v for k, v in assignment.metrics.items()},
        "trends": keywords.emerging_and_fading(keyword_stats),
        "year_layers": heatmap.year_heatmap(
            coords, [p.year for p in papers], grid_size=cfg.heatmap_grid
        ),
    }

    result = AnalysisResult(
        project_id=project_id,
        name=name or f"{len(papers)} papers, {assignment.n_clusters} clusters",
        config=cfg,
        embedding_model=embedding_result.model,
        reducer=projection.reducer,
        clusterer=assignment.method,
        points=points_out,
        clusters=cluster_infos,
        keywords=keyword_stats,
        gaps=gap_candidates,
        heatmap=heat,
        metrics=metrics,
        n_papers=len(papers),
        n_clusters=assignment.n_clusters,
        warnings=warnings,
        created_at=utc_now_iso(),
    )

    if persist:
        progress(0.95, "saving analysis")
        # The fitted reducer is stored only when it can place new points, and
        # only when the embedding space is portable - otherwise incremental
        # placement would put a paper into a space fitted on a different corpus.
        projector_payload = None
        if projection.supports_transform and not embedding_result.corpus_relative:
            projector_payload = {
                "model": projection.model,
                "scaling": projection.metrics.get("scaling", {}),
                "embedding_model": embedding_result.model,
                "reducer": projection.reducer,
            }
        result = analyses_store.save_analysis(
            result, projector=projector_payload, paper_ids=[p.id for p in papers]
        )

    progress(1.0, f"analysis complete: {assignment.n_clusters} clusters, "
                  f"{len(gap_candidates)} gap candidates")
    log.info(
        "analysis %s: %s papers, %s clusters, %s gaps, %s backend, %s reducer "
        "(%.1fs)",
        result.id, len(papers), assignment.n_clusters, len(gap_candidates),
        embedding_result.backend, projection.reducer, time.perf_counter() - started,
    )
    return result


def analyse_paper_ids(
    paper_ids: list[str],
    *,
    config: AnalysisConfig | None = None,
    project_id: str = "",
    name: str = "",
    job: JobContext | None = None,
) -> AnalysisResult:
    """Load papers by id and analyse them."""
    loaded = papers_store.get_many(paper_ids)
    if not loaded:
        raise ValidationError("none of the given paper ids exist in the library")
    if len(loaded) < len(paper_ids):
        log.warning(
            "%s of %s requested papers were not found and are excluded",
            len(paper_ids) - len(loaded), len(paper_ids),
        )
    return build_analysis(
        loaded, config=config, project_id=project_id, name=name, job=job
    )


def analyse_project(
    project_id: str,
    *,
    config: AnalysisConfig | None = None,
    name: str = "",
    job: JobContext | None = None,
) -> AnalysisResult:
    """Analyse every paper linked to a project."""
    paper_ids = papers_store.project_paper_ids(project_id)
    if not paper_ids:
        raise ValidationError(
            "this project has no papers yet - run a search first"
        )
    return analyse_paper_ids(
        paper_ids, config=config, project_id=project_id, name=name, job=job
    )


def submit_analysis(
    *,
    project_id: str = "",
    paper_ids: list[str] | None = None,
    config_overrides: dict[str, Any] | None = None,
    name: str = "",
) -> str:
    """Queue an analysis as a background job. Returns the job id.

    Analysis is CPU-bound and can take tens of seconds (UMAP on a few thousand
    papers), so it never runs inside a request.
    """
    from ..core.jobs import manager

    cfg = config_from_settings(config_overrides)

    def work(ctx: JobContext) -> dict[str, Any]:
        if paper_ids:
            result = analyse_paper_ids(
                paper_ids, config=cfg, project_id=project_id, name=name, job=ctx
            )
        else:
            result = analyse_project(project_id, config=cfg, name=name, job=ctx)
        # Return a summary, not the full payload: the UI fetches the analysis by
        # id, and a 40x40 grid plus layers is megabytes of JSON.
        return {
            "analysis_id": result.id,
            "n_papers": result.n_papers,
            "n_clusters": result.n_clusters,
            "n_gaps": len(result.gaps),
            "warnings": result.warnings,
        }

    handle = manager.submit(
        "analysis", work,
        payload={"project_id": project_id, "paper_count": len(paper_ids or [])},
        project_id=project_id or None,
    )
    return handle.id


def describe_capabilities() -> dict[str, Any]:
    """What the analysis stack can currently do, for the UI settings panel."""
    return {
        "embedding_backends": embeddings.describe_backends(),
        "reducers": reduce.describe_reducers(),
        "clusterers": cluster.describe_clusterers(),
        "gap_detectors": gaps.describe_detectors(),
        "min_papers_for_full_analysis": MIN_PAPERS_FOR_FULL_ANALYSIS,
        "optional_stack_installed": {
            "sentence_transformers": embeddings.sentence_transformers_available(),
            "umap": reduce.umap_available(),
            "hdbscan": cluster.hdbscan_available(),
        },
    }

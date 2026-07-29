"""Analysis routes: build the landscape, place ideas in it, read the graph.

The heavy endpoint (``POST /api/analysis``) is always a background job - UMAP over
a few thousand papers takes tens of seconds. Reads are direct.

Landscape payloads are large (a 40x40 grid with 12 keyword layers plus z-slices
is several MB of JSON), so ``GET /api/analysis/{id}`` takes flags to omit the
parts a given view does not need.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ...core.errors import NotFoundError, ValidationError
from ...core.logging_setup import get_logger
from ...core.models import PositionResult
from ...store import analyses as analyses_store
from ...store import papers as papers_store

log = get_logger(__name__)
router = APIRouter(prefix="/api/analysis", tags=["analysis"])


class AnalysisRequest(BaseModel):
    project_id: str = ""
    paper_ids: list[str] = Field(default_factory=list)
    name: str = ""
    # Config overrides; None means "use the setting".
    embedding_backend: str | None = None
    reducer: str | None = None
    clusterer: str | None = None
    dimensions: int | None = None
    n_neighbors: int | None = None
    min_dist: float | None = None
    min_cluster_size: int | None = None
    n_clusters: int | None = None
    keyword_top_k: int | None = None
    heatmap_grid: int | None = None
    detect_gaps: bool | None = None
    gap_min_score: float | None = None

    def overrides(self) -> dict[str, Any]:
        return {
            k: v for k, v in self.model_dump(
                exclude={"project_id", "paper_ids", "name"}
            ).items() if v is not None
        }


@router.get("/capabilities")
def capabilities() -> dict[str, Any]:
    """Which backends, reducers, clusterers and detectors are usable here."""
    from ...analysis import pipeline

    return pipeline.describe_capabilities()


@router.post("")
def submit_analysis(request: AnalysisRequest) -> dict[str, Any]:
    """Queue a landscape build."""
    from ...analysis import pipeline

    if not request.project_id and not request.paper_ids:
        raise ValidationError("provide a project_id or an explicit paper_ids list")
    if request.project_id and not request.paper_ids:
        count = papers_store.project_paper_count(request.project_id)
        if count == 0:
            raise ValidationError(
                "this project has no papers yet. Run a search first, or import a "
                "bibliography."
            )
    job_id = pipeline.submit_analysis(
        project_id=request.project_id,
        paper_ids=request.paper_ids or None,
        config_overrides=request.overrides(),
        name=request.name,
    )
    return {
        "job_id": job_id,
        "note": "subscribe to /api/system/events for progress; the result payload "
                "contains the analysis_id",
    }


@router.post("/sync")
def analyse_now(request: AnalysisRequest) -> dict[str, Any]:
    """Build a landscape synchronously. For small sets, scripts and tests."""
    from ...analysis import pipeline

    config = pipeline.config_from_settings(request.overrides())
    if request.paper_ids:
        result = pipeline.analyse_paper_ids(
            request.paper_ids, config=config,
            project_id=request.project_id, name=request.name,
        )
    elif request.project_id:
        result = pipeline.analyse_project(
            request.project_id, config=config, name=request.name
        )
    else:
        raise ValidationError("provide a project_id or paper_ids")
    return _summary(result)


def _summary(result: Any) -> dict[str, Any]:
    """Compact analysis description, without the bulky grid payloads."""
    return {
        "analysis_id": result.id,
        "project_id": result.project_id,
        "name": result.name,
        "n_papers": result.n_papers,
        "n_clusters": result.n_clusters,
        "embedding_model": result.embedding_model,
        "reducer": result.reducer,
        "clusterer": result.clusterer,
        "metrics": result.metrics,
        "clusters": [c.model_dump() for c in result.clusters],
        "gaps": [g.model_dump() for g in result.gaps],
        "keyword_count": len(result.keywords),
        "warnings": result.warnings,
        "created_at": result.created_at,
    }


@router.get("")
def list_analyses(
    project_id: str = "", limit: int = Query(50, le=200)
) -> dict[str, Any]:
    return {"items": analyses_store.list_analyses(project_id, limit=limit)}


@router.get("/{analysis_id}")
def get_analysis(
    analysis_id: str,
    include_points: bool = Query(True),
    include_heatmap: bool = Query(True),
    include_layers: bool = Query(False, description="keyword heatmap layers (large)"),
    include_papers: bool = Query(False, description="inline the paper records"),
) -> dict[str, Any]:
    """One landscape. Flags control payload size.

    ``include_layers`` is off by default: twelve 40x40 float grids is roughly a
    megabyte of JSON, and the 3D view does not need them until the user switches
    to a keyword layer.
    """
    result = analyses_store.get_analysis(analysis_id, with_points=include_points)
    if result is None:
        raise NotFoundError(f"analysis {analysis_id} not found")

    payload: dict[str, Any] = {
        **_summary(result),
        "config": result.config.model_dump(),
        "keywords": [k.model_dump() for k in result.keywords],
        "points": [p.model_dump() for p in result.points] if include_points else [],
    }
    if include_heatmap:
        heatmap = result.heatmap.model_dump()
        if not include_layers:
            heatmap["layers"] = {}
            heatmap["layer_names"] = list(result.heatmap.layers.keys())
        payload["heatmap"] = heatmap
    if include_papers and include_points:
        papers = papers_store.get_many([p.paper_id for p in result.points])
        payload["papers"] = [p.model_dump() for p in papers]
        found = {p.id for p in papers}
        payload["missing_papers"] = [
            p.paper_id for p in result.points if p.paper_id not in found
        ]
    return payload


@router.get("/{analysis_id}/layer/{term}")
def get_layer(analysis_id: str, term: str) -> dict[str, Any]:
    """One keyword heatmap layer, fetched on demand."""
    result = analyses_store.get_analysis(analysis_id, with_points=False)
    if result is None:
        raise NotFoundError(f"analysis {analysis_id} not found")
    grid = result.heatmap.layers.get(term)
    if grid is None:
        raise NotFoundError(
            f"no layer for '{term}'. Available: "
            f"{', '.join(list(result.heatmap.layers)[:20])}"
        )
    return {
        "term": term, "grid": grid, "grid_size": result.heatmap.grid_size,
        "bounds": result.heatmap.bounds,
    }


@router.get("/{analysis_id}/papers")
def analysis_papers(analysis_id: str) -> dict[str, Any]:
    """The papers in a landscape, with their coordinates and cluster.

    One call for the table view beside the 3D plot, so it never has to join two
    responses client-side.
    """
    result = analyses_store.get_analysis(analysis_id)
    if result is None:
        raise NotFoundError(f"analysis {analysis_id} not found")
    papers = {p.id: p for p in papers_store.get_many([p.paper_id for p in result.points])}
    cluster_labels = {c.id: c.label for c in result.clusters}
    items = []
    for point in result.points:
        paper = papers.get(point.paper_id)
        items.append({
            "paper_id": point.paper_id,
            "x": point.x, "y": point.y, "z": point.z,
            "cluster": point.cluster,
            "cluster_label": cluster_labels.get(point.cluster, ""),
            "outlier": point.outlier,
            "density": point.density,
            "is_seed": point.is_seed,
            "title": paper.title if paper else "(removed from library)",
            "year": paper.year if paper else None,
            "venue": paper.venue if paper else "",
            "citations": paper.citation_count if paper else 0,
            "authors": paper.author_names(3) if paper else [],
            "origin": paper.origin if paper else "",
            "missing": paper is None,
        })
    return {"analysis_id": analysis_id, "items": items}


@router.delete("/{analysis_id}")
def delete_analysis(analysis_id: str) -> dict[str, Any]:
    """Delete a landscape. Papers and embeddings are untouched."""
    return {
        "deleted": analyses_store.delete_analysis(analysis_id),
        "note": "papers and cached embeddings are kept",
    }


# ---------------------------------------------- incremental add / remove


class PlaceIdeaRequest(BaseModel):
    title: str
    abstract: str = ""
    keywords: list[str] = Field(default_factory=list)
    project_id: str = ""
    persist: bool = True


@router.post("/{analysis_id}/place-idea", response_model=PositionResult)
def place_idea(analysis_id: str, request: PlaceIdeaRequest) -> PositionResult:
    """Place a free-text idea into an existing landscape and interpret where it lands.

    The existing points do not move: the stored reducer is reused. When the
    landscape was built with corpus-relative embeddings (currently TF-IDF) this
    returns a 409 explaining that the analysis must be re-run instead - placing a
    vector fitted on a different corpus would be meaningless.
    """
    from ...analysis import incremental

    result = incremental.place_idea(
        analysis_id,
        title=request.title,
        abstract=request.abstract,
        keywords=request.keywords,
        project_id=request.project_id,
        persist=request.persist,
    )
    return result


class PlacePaperRequest(BaseModel):
    paper_id: str
    persist: bool = True
    mark_as_seed: bool = True


@router.post("/{analysis_id}/place-paper", response_model=PositionResult)
def place_paper(analysis_id: str, request: PlacePaperRequest) -> PositionResult:
    """Place an existing library paper into a landscape it was not part of."""
    from ...analysis import incremental

    paper = papers_store.get(request.paper_id)
    if paper is None:
        raise NotFoundError(f"paper {request.paper_id} not found")
    result = incremental.place_paper(
        analysis_id, paper, persist=request.persist,
        mark_as_seed=request.mark_as_seed,
    )
    return result


class RemovePointsRequest(BaseModel):
    paper_ids: list[str]


@router.post("/{analysis_id}/remove-points")
def remove_points(analysis_id: str, request: RemovePointsRequest) -> dict[str, Any]:
    """Remove points from a landscape without recomputing it."""
    from ...analysis import incremental

    return incremental.remove_from_analysis(analysis_id, request.paper_ids)


# ------------------------------------------------------------------- graph


@router.get("/{analysis_id}/graph")
def analysis_graph(analysis_id: str) -> dict[str, Any]:
    """Citation, co-citation, coupling and co-authorship view of the same papers.

    Reference data comes only from OpenAlex, so ``citation.coverage`` is reported
    and should be checked before drawing conclusions from the citation metrics.
    """
    from ...analysis import graph as graph_module

    paper_ids = analyses_store.analysis_paper_ids(analysis_id)
    if not paper_ids:
        raise NotFoundError(f"analysis {analysis_id} has no papers")
    papers = papers_store.get_many(paper_ids)
    return {"analysis_id": analysis_id, **graph_module.analyse_graph(papers)}


@router.get("/project/{project_id}/graph")
def project_graph(project_id: str) -> dict[str, Any]:
    """The relational view for a whole project, without needing an analysis."""
    from ...analysis import graph as graph_module

    paper_ids = papers_store.project_paper_ids(project_id)
    if not paper_ids:
        raise ValidationError("this project has no papers yet")
    return {
        "project_id": project_id,
        **graph_module.analyse_graph(papers_store.get_many(paper_ids)),
    }


class ClusterLabelRequest(BaseModel):
    cluster_ids: list[int] = Field(default_factory=list)


@router.post("/{analysis_id}/label-clusters")
async def label_clusters(
    analysis_id: str, request: ClusterLabelRequest
) -> dict[str, Any]:
    """Replace mechanical cluster labels with LLM-written ones.

    Optional by design: the keyword-derived labels are always present so the map
    is readable with no model configured. This just makes them nicer.
    """
    from ...llm import client as llm_client
    from ...llm import registry as llm_registry

    result = analyses_store.get_analysis(analysis_id, with_points=False)
    if result is None:
        raise NotFoundError(f"analysis {analysis_id} not found")
    if not llm_registry.has_any_provider():
        raise ValidationError(
            "no LLM provider is configured, so clusters cannot be relabelled. The "
            "existing keyword-based labels remain usable."
        )

    targets = [
        c for c in result.clusters
        if not request.cluster_ids or c.id in request.cluster_ids
    ]
    if not targets:
        return {"updated": 0, "clusters": []}

    described = "\n".join(
        f"Cluster {c.id} ({c.size} papers, {c.year_min}-{c.year_max}): "
        f"{', '.join(c.keywords[:10])}"
        for c in targets
    )
    payload = await llm_client.complete_json(
        f"Name each cluster of research papers with a specific 2-5 word topic "
        f"label. Base the label only on the keywords given.\n\n{described}",
        system=(
            'Respond with STRICT JSON: {"labels": [{"id": 0, "label": "...", '
            '"label_zh": "...", "summary": "one sentence on what this cluster '
            'studies"}]}'
        ),
        purpose="cluster_labelling",
        max_tokens=1500,
    )
    by_id = {c.id: c for c in result.clusters}
    updated = 0
    for entry in (payload or {}).get("labels", []):
        cluster = by_id.get(int(entry.get("id", -1)))
        if cluster is None:
            continue
        if entry.get("label"):
            cluster.label = str(entry["label"])[:120]
        if entry.get("label_zh"):
            cluster.label_zh = str(entry["label_zh"])[:120]
        if entry.get("summary"):
            cluster.summary = str(entry["summary"])[:500]
        updated += 1
    analyses_store.save_analysis(
        result, paper_ids=analyses_store.analysis_paper_ids(analysis_id)
    )
    return {
        "updated": updated,
        "clusters": [c.model_dump() for c in result.clusters],
    }

"""Dimensionality reduction: high-dimensional embeddings -> 3D coordinates.

This is what makes the "3D orientation of the retrieved papers" requirement
concrete. The method matters, because different reducers answer different
questions:

* **UMAP** (default when installed) - preserves local neighbourhoods and much of
  the global structure. This is the method used by the published research-mapping
  work (the PubMed landscape of ~21M abstracts, BERTopic, PaperLens and similar
  tools all use UMAP + HDBSCAN), so it is the right default for a literature map:
  clusters come out visually separated and *reproducibly* placed.
* **PCA** - linear, exact, instant, and deterministic. Always available. Axes are
  interpretable (directions of maximum variance) but dense topics overlap.
* **t-SNE** - excellent local separation, but distances between clusters are not
  meaningful and it cannot embed new points, which breaks the "add my idea to the
  existing map" requirement. Offered, not defaulted.
* **MDS** - preserves pairwise distances globally; useful for small sets where
  "how far apart are these two subfields" should be readable off the axes.

A fitted reducer is returned alongside the coordinates and persisted by
``store.analyses``, so a paper added later lands in the *same* space instead of
forcing a recompute that would move every existing point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..core.errors import ValidationError
from ..core.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class Projection:
    """Result of a reduction, plus everything needed to reuse it later."""

    coords: np.ndarray                    # (n, dims) float32
    reducer: str                          # umap | pca | tsne | mds | passthrough
    dims: int
    model: Any = None                     # fitted object, pickled by the store
    supports_transform: bool = False      # can it place new points?
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def umap_available() -> bool:
    try:
        import umap  # noqa: F401
    except Exception:  # noqa: BLE001 - numba/llvmlite failures are common
        return False
    return True


def resolve_reducer(requested: str, n_samples: int) -> tuple[str, list[str]]:
    """Choose a reducer given the request and the data size.

    Sample count is a hard constraint, not a preference: UMAP needs more points
    than ``n_neighbors``, and t-SNE's perplexity must be below ``n_samples``. A
    5-paper collection can only get PCA, so say so rather than crashing inside a
    library.
    """
    choice = (requested or "auto").lower()
    warnings: list[str] = []

    if n_samples < 4:
        if choice not in ("auto", "pca", "passthrough"):
            warnings.append(
                f"{choice} needs at least 4 papers (got {n_samples}); using PCA"
            )
        return ("pca" if n_samples >= 2 else "passthrough"), warnings

    if choice == "umap":
        if umap_available():
            return "umap", warnings
        warnings.append(
            'umap-learn is not installed; using PCA. Install with: '
            'pip install "papercreator[analysis]"'
        )
        return "pca", warnings
    if choice in ("pca", "tsne", "mds"):
        if choice == "tsne" and n_samples < 10:
            warnings.append(f"t-SNE needs ~10+ papers (got {n_samples}); using PCA")
            return "pca", warnings
        return choice, warnings

    # auto
    if umap_available() and n_samples >= 10:
        return "umap", warnings
    if n_samples < 10 and umap_available():
        warnings.append(
            f"only {n_samples} papers: using PCA, which is more stable than UMAP "
            "on very small sets"
        )
        return "pca", warnings
    warnings.append(
        "using PCA for layout. UMAP separates topical clusters much better: "
        'pip install "papercreator[analysis]"'
    )
    return "pca", warnings


def _reduce_umap(
    vectors: np.ndarray,
    dims: int,
    *,
    n_neighbors: int,
    min_dist: float,
    metric: str,
    random_state: int,
) -> Projection:
    import umap

    n_samples = vectors.shape[0]
    # n_neighbors must be < n_samples; UMAP raises otherwise. Clamp rather than
    # fail, and tell the user their setting was adjusted.
    effective_neighbors = max(2, min(n_neighbors, n_samples - 1))
    warnings: list[str] = []
    if effective_neighbors != n_neighbors:
        warnings.append(
            f"n_neighbors reduced from {n_neighbors} to {effective_neighbors} "
            f"for {n_samples} papers"
        )
    reducer = umap.UMAP(
        n_components=dims,
        n_neighbors=effective_neighbors,
        min_dist=min_dist,
        metric=metric,
        # A fixed seed makes the map reproducible, which matters because the user
        # builds a mental picture of where things are. UMAP warns that this
        # disables parallelism; for these corpus sizes that is an acceptable
        # trade for stability.
        random_state=random_state,
        n_epochs=None,
        init="spectral",
        verbose=False,
    )
    coords = reducer.fit_transform(vectors)
    return Projection(
        coords=np.asarray(coords, dtype=np.float32),
        reducer="umap",
        dims=dims,
        model=reducer,
        supports_transform=True,
        metrics={"n_neighbors": effective_neighbors, "min_dist": min_dist,
                 "metric": metric},
        warnings=warnings,
    )


def _reduce_pca(vectors: np.ndarray, dims: int, random_state: int) -> Projection:
    from sklearn.decomposition import PCA

    components = min(dims, vectors.shape[0], vectors.shape[1])
    model = PCA(n_components=components, random_state=random_state)
    coords = model.fit_transform(vectors)
    if coords.shape[1] < dims:
        # Pad so the frontend always receives x, y, z.
        padding = np.zeros((coords.shape[0], dims - coords.shape[1]), dtype=np.float32)
        coords = np.hstack([coords, padding])
    return Projection(
        coords=np.asarray(coords, dtype=np.float32),
        reducer="pca",
        dims=dims,
        model=model,
        supports_transform=True,
        metrics={
            "explained_variance_ratio": [
                round(float(v), 4) for v in model.explained_variance_ratio_
            ],
            "total_explained": round(float(model.explained_variance_ratio_.sum()), 4),
        },
    )


def _reduce_tsne(
    vectors: np.ndarray, dims: int, *, random_state: int, metric: str
) -> Projection:
    from sklearn.manifold import TSNE

    n_samples = vectors.shape[0]
    # Perplexity must be < n_samples; 30 is the usual default.
    perplexity = max(5.0, min(30.0, (n_samples - 1) / 3.0))
    model = TSNE(
        n_components=dims,
        perplexity=perplexity,
        metric=metric if metric != "cosine" else "cosine",
        init="pca",
        random_state=random_state,
        max_iter=1000,
    )
    coords = model.fit_transform(vectors)
    return Projection(
        coords=np.asarray(coords, dtype=np.float32),
        reducer="tsne",
        dims=dims,
        model=None,
        # t-SNE has no out-of-sample transform. Incremental placement therefore
        # falls back to neighbour interpolation, which is why it is not default.
        supports_transform=False,
        metrics={"perplexity": perplexity, "kl_divergence": float(
            getattr(model, "kl_divergence_", 0.0)
        )},
        warnings=[
            "t-SNE cannot place new papers into an existing map; adding an idea "
            "later will use neighbour interpolation instead"
        ],
    )


def _reduce_mds(vectors: np.ndarray, dims: int, random_state: int) -> Projection:
    from sklearn.manifold import MDS

    model = MDS(
        n_components=dims,
        random_state=random_state,
        normalized_stress="auto",
        n_init=2,
        max_iter=300,
    )
    coords = model.fit_transform(vectors)
    return Projection(
        coords=np.asarray(coords, dtype=np.float32),
        reducer="mds",
        dims=dims,
        model=None,
        supports_transform=False,
        metrics={"stress": float(getattr(model, "stress_", 0.0))},
        warnings=["MDS cannot place new papers into an existing map"],
    )


def reduce_vectors(
    vectors: np.ndarray,
    *,
    dims: int = 3,
    reducer: str = "auto",
    n_neighbors: int = 15,
    min_dist: float = 0.1,
    metric: str = "cosine",
    random_state: int = 42,
) -> Projection:
    """Project embeddings to ``dims`` dimensions.

    Output coordinates are normalised into a cube roughly [-10, 10] per axis so
    the frontend camera, point sizes and grid resolution work without knowing
    which reducer ran.
    """
    if vectors.size == 0:
        raise ValidationError("cannot reduce an empty embedding matrix")
    if vectors.ndim != 2:
        raise ValidationError(f"expected a 2D embedding matrix, got shape {vectors.shape}")

    n_samples = vectors.shape[0]
    chosen, warnings = resolve_reducer(reducer, n_samples)

    if chosen == "passthrough":
        # 1 sample (or degenerate input): place at the origin. Downstream code
        # handles a single point, and failing here would break a legitimate
        # "analyse my one idea" call.
        coords = np.zeros((n_samples, dims), dtype=np.float32)
        return Projection(
            coords=coords, reducer="passthrough", dims=dims, model=None,
            supports_transform=False,
            warnings=[*warnings, f"only {n_samples} paper(s): no layout computed"],
        )

    if chosen == "umap":
        projection = _reduce_umap(
            vectors, dims, n_neighbors=n_neighbors, min_dist=min_dist,
            metric=metric, random_state=random_state,
        )
    elif chosen == "tsne":
        projection = _reduce_tsne(
            vectors, dims, random_state=random_state, metric=metric
        )
    elif chosen == "mds":
        projection = _reduce_mds(vectors, dims, random_state)
    else:
        projection = _reduce_pca(vectors, dims, random_state)

    projection.warnings = [*warnings, *projection.warnings]
    projection.coords, scale_info = normalise_coords(projection.coords)
    projection.metrics["scaling"] = scale_info
    log.info(
        "reduced %s x %s -> %sD via %s", n_samples, vectors.shape[1], dims,
        projection.reducer,
    )
    return projection


def normalise_coords(
    coords: np.ndarray, target_half_range: float = 10.0
) -> tuple[np.ndarray, dict[str, Any]]:
    """Centre coordinates and scale isotropically into a fixed cube.

    Isotropic (one shared factor, not per-axis) so relative distances survive -
    per-axis scaling would distort the shape the reducer produced, making
    "these two clusters are far apart" unreadable.

    The returned ``scale_info`` lets :mod:`analysis.incremental` apply the exact
    same transform to a newly projected point.
    """
    if coords.size == 0:
        return coords, {}
    centre = coords.mean(axis=0)
    centred = coords - centre
    max_extent = float(np.abs(centred).max())
    factor = (target_half_range / max_extent) if max_extent > 1e-9 else 1.0
    return (
        (centred * factor).astype(np.float32),
        {
            "centre": [round(float(c), 6) for c in centre],
            "factor": round(float(factor), 6),
            "target_half_range": target_half_range,
        },
    )


def apply_normalisation(coords: np.ndarray, scale_info: dict[str, Any]) -> np.ndarray:
    """Re-apply a stored normalisation to new raw coordinates."""
    if not scale_info:
        return np.asarray(coords, dtype=np.float32)
    centre = np.asarray(scale_info.get("centre") or [], dtype=np.float32)
    factor = float(scale_info.get("factor") or 1.0)
    array = np.asarray(coords, dtype=np.float32)
    if centre.size and centre.size == array.shape[-1]:
        array = array - centre
    return (array * factor).astype(np.float32)


def trustworthiness_score(
    vectors: np.ndarray, coords: np.ndarray, n_neighbors: int = 10
) -> float:
    """How well the projection preserves original neighbourhoods, 0..1.

    Reported in the UI so the user knows whether to trust visual proximity.
    Computed on a sample for large corpora because the exact measure is O(n²) in
    memory.
    """
    try:
        from sklearn.manifold import trustworthiness
    except ImportError:
        return 0.0
    n_samples = vectors.shape[0]
    if n_samples < n_neighbors + 2:
        return 0.0
    if n_samples > 3000:
        rng = np.random.default_rng(42)
        sample = rng.choice(n_samples, 3000, replace=False)
        vectors, coords = vectors[sample], coords[sample]
    try:
        return round(float(trustworthiness(
            vectors, coords, n_neighbors=min(n_neighbors, vectors.shape[0] - 2)
        )), 4)
    except (ValueError, MemoryError) as exc:
        log.debug("trustworthiness could not be computed: %s", exc)
        return 0.0


def describe_reducers() -> list[dict[str, Any]]:
    """Reducer catalogue for the analysis settings panel."""
    return [
        {
            "id": "umap",
            "name": "UMAP",
            "available": umap_available(),
            "supports_new_points": True,
            "preserves": "local neighbourhoods + much global structure",
            "requirement": 'pip install "papercreator[analysis]"',
            "note": "the standard choice for literature maps; clusters separate "
                    "clearly and placement is reproducible",
        },
        {
            "id": "pca",
            "name": "PCA",
            "available": True,
            "supports_new_points": True,
            "preserves": "global variance (linear)",
            "requirement": "included in the base install",
            "note": "instant and deterministic; dense topics overlap more",
        },
        {
            "id": "tsne",
            "name": "t-SNE",
            "available": True,
            "supports_new_points": False,
            "preserves": "local neighbourhoods only",
            "requirement": "included in the base install",
            "note": "best visual separation, but between-cluster distances are "
                    "not meaningful and new papers cannot be added exactly",
        },
        {
            "id": "mds",
            "name": "MDS",
            "available": True,
            "supports_new_points": False,
            "preserves": "pairwise distances globally",
            "requirement": "included in the base install",
            "note": "readable absolute distances; slow beyond a few hundred papers",
        },
    ]

"""Clustering: grouping papers into topical clusters.

Clusters are what turn a cloud of dots into a readable map - they become the
coloured regions, the legend entries, and the units that gap detection compares.

Methods:

* **HDBSCAN** (default when installed) - density-based, finds the number of
  clusters itself, and marks genuinely isolated papers as noise instead of
  forcing them into a group. That last property is why it pairs with UMAP in
  essentially every published topic-mapping pipeline: a literature corpus really
  does contain outliers, and pretending otherwise distorts every cluster it
  touches.
* **KMeans** - always available, needs ``k``. Fast and predictable; assigns every
  paper, so no outlier concept. :func:`estimate_k` picks ``k`` by silhouette
  score when the user does not.
* **Agglomerative** - hierarchical with a distance threshold; useful when the
  user wants "split until clusters are at most this broad".

Clustering runs on the **embedding vectors**, not the 3D coordinates. Reducing to
3D loses information, and clustering the projection would find groups that are
artefacts of the layout rather than of the literature.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..core.logging_setup import get_logger
from ..core.models import Paper

log = get_logger(__name__)

NOISE_LABEL = -1


@dataclass
class ClusterAssignment:
    labels: np.ndarray                       # (n,) int, -1 = noise
    method: str
    n_clusters: int
    outlier_scores: np.ndarray               # (n,) float, 0 when unsupported
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def hdbscan_available() -> bool:
    try:
        import hdbscan  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        pass
    # scikit-learn >= 1.3 ships its own HDBSCAN implementation.
    try:
        from sklearn.cluster import HDBSCAN  # noqa: F401

        return True
    except ImportError:
        return False


def resolve_clusterer(requested: str, n_samples: int) -> tuple[str, list[str]]:
    choice = (requested or "auto").lower()
    warnings: list[str] = []
    if n_samples < 4:
        return "single", [f"only {n_samples} papers: treating them as one cluster"]
    if choice == "hdbscan":
        if hdbscan_available():
            return "hdbscan", warnings
        warnings.append(
            'HDBSCAN is unavailable; using KMeans. Install with: '
            'pip install "papercreator[analysis]" (or upgrade scikit-learn to >=1.3)'
        )
        return "kmeans", warnings
    if choice in ("kmeans", "agglomerative"):
        return choice, warnings
    # auto
    if hdbscan_available() and n_samples >= 15:
        return "hdbscan", warnings
    if n_samples < 15:
        return "kmeans", [
            f"{n_samples} papers is too few for density-based clustering; "
            "using KMeans"
        ]
    warnings.append("using KMeans; HDBSCAN finds cluster counts automatically and "
                    "identifies outliers")
    return "kmeans", warnings


def estimate_k(vectors: np.ndarray, *, k_min: int = 2, k_max: int = 12) -> int:
    """Choose ``k`` for KMeans by maximising the silhouette score.

    Silhouette rather than elbow/inertia because it has a comparable scale across
    ``k`` and does not require eyeballing a curve. The search is bounded by
    ``sqrt(n/2)``, the standard rule of thumb, so a 40-paper set does not get 12
    clusters of three papers each.
    """
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    n_samples = vectors.shape[0]
    upper = int(max(k_min, min(k_max, np.sqrt(n_samples / 2))))
    if upper <= k_min:
        return k_min
    best_k, best_score = k_min, -1.0
    for k in range(k_min, upper + 1):
        try:
            labels = KMeans(n_clusters=k, n_init=4, random_state=42).fit_predict(vectors)
            if len(set(labels)) < 2:
                continue
            score = silhouette_score(vectors, labels, metric="cosine")
        except ValueError:
            continue
        if score > best_score:
            best_k, best_score = k, score
    log.info("estimated k=%s (silhouette %.3f)", best_k, best_score)
    return best_k


def _cluster_hdbscan(
    vectors: np.ndarray, min_cluster_size: int
) -> ClusterAssignment:
    n_samples = vectors.shape[0]
    effective_min = max(2, min(min_cluster_size, max(2, n_samples // 4)))
    warnings: list[str] = []
    if effective_min != min_cluster_size:
        warnings.append(
            f"min_cluster_size reduced from {min_cluster_size} to {effective_min} "
            f"for {n_samples} papers"
        )

    labels: np.ndarray
    outliers = np.zeros(n_samples, dtype=np.float32)
    try:
        import hdbscan as hdbscan_lib

        model = hdbscan_lib.HDBSCAN(
            min_cluster_size=effective_min,
            min_samples=max(1, effective_min // 2),
            # Euclidean on L2-normalised vectors is monotonically related to
            # cosine distance, and the hdbscan library's cosine support is
            # limited; the vectors are normalised upstream precisely for this.
            metric="euclidean",
            cluster_selection_method="eom",
            prediction_data=True,
        )
        labels = model.fit_predict(vectors)
        if getattr(model, "outlier_scores_", None) is not None:
            outliers = np.nan_to_num(
                np.asarray(model.outlier_scores_, dtype=np.float32)
            )
        implementation = "hdbscan"
    except Exception as exc:  # noqa: BLE001 - fall through to sklearn's version
        log.debug("hdbscan library unavailable/failed (%s); trying sklearn", exc)
        from sklearn.cluster import HDBSCAN

        model = HDBSCAN(
            min_cluster_size=effective_min,
            min_samples=max(1, effective_min // 2),
            metric="euclidean",
            cluster_selection_method="eom",
        )
        labels = model.fit_predict(vectors)
        implementation = "sklearn.HDBSCAN"

    labels = np.asarray(labels, dtype=int)
    unique = sorted({int(v) for v in labels if v != NOISE_LABEL})
    noise_count = int((labels == NOISE_LABEL).sum())
    if not unique:
        warnings.append(
            "HDBSCAN found no dense cluster - the corpus is either very diverse "
            "or too small; falling back to KMeans"
        )
        return _cluster_kmeans(vectors, 0)
    if noise_count > n_samples * 0.6:
        warnings.append(
            f"{noise_count} of {n_samples} papers were marked as outliers; the "
            "topic set may be too heterogeneous for density clustering"
        )
    return ClusterAssignment(
        labels=labels,
        method="hdbscan",
        n_clusters=len(unique),
        outlier_scores=outliers,
        metrics={"implementation": implementation, "min_cluster_size": effective_min,
                 "noise": noise_count},
        warnings=warnings,
    )


def _cluster_kmeans(vectors: np.ndarray, n_clusters: int) -> ClusterAssignment:
    from sklearn.cluster import KMeans

    n_samples = vectors.shape[0]
    k = n_clusters if n_clusters > 0 else estimate_k(vectors)
    k = max(1, min(k, n_samples))
    model = KMeans(n_clusters=k, n_init=10, random_state=42)
    labels = model.fit_predict(vectors)
    # KMeans has no outlier notion; use normalised distance to the assigned
    # centroid so the UI can still de-emphasise loosely-attached papers.
    distances = np.linalg.norm(vectors - model.cluster_centers_[labels], axis=1)
    span = float(distances.max()) or 1.0
    return ClusterAssignment(
        labels=np.asarray(labels, dtype=int),
        method="kmeans",
        n_clusters=int(len(set(labels))),
        outlier_scores=(distances / span).astype(np.float32),
        metrics={"k": k, "inertia": round(float(model.inertia_), 3)},
    )


def _cluster_agglomerative(
    vectors: np.ndarray, n_clusters: int, min_cluster_size: int
) -> ClusterAssignment:
    from sklearn.cluster import AgglomerativeClustering

    n_samples = vectors.shape[0]
    if n_clusters > 0:
        model = AgglomerativeClustering(
            n_clusters=min(n_clusters, n_samples), metric="cosine", linkage="average"
        )
    else:
        # 0.5 cosine distance ~ 60 degrees apart: empirically a reasonable
        # "different subtopic" boundary for paper abstracts.
        model = AgglomerativeClustering(
            n_clusters=None, distance_threshold=0.5, metric="cosine",
            linkage="average",
        )
    labels = np.asarray(model.fit_predict(vectors), dtype=int)

    # Demote clusters below the size floor to noise so the legend stays useful.
    counts = {int(v): int((labels == v).sum()) for v in set(labels)}
    warnings: list[str] = []
    small = [label for label, count in counts.items() if count < max(2, min_cluster_size)]
    if small:
        for label in small:
            labels[labels == label] = NOISE_LABEL
        warnings.append(
            f"{len(small)} cluster(s) below the size floor were marked as outliers"
        )
    return ClusterAssignment(
        labels=labels,
        method="agglomerative",
        n_clusters=len({int(v) for v in labels if v != NOISE_LABEL}),
        outlier_scores=np.zeros(n_samples, dtype=np.float32),
        metrics={"threshold": 0.5 if n_clusters <= 0 else None},
        warnings=warnings,
    )


def cluster_vectors(
    vectors: np.ndarray,
    *,
    method: str = "auto",
    min_cluster_size: int = 5,
    n_clusters: int = 0,
) -> ClusterAssignment:
    """Cluster embeddings. Labels are relabelled so 0 is the largest cluster.

    Stable, size-ordered labels matter for the UI: cluster 0 always gets the
    first colour, and a re-run with one extra paper should not reshuffle the
    palette.
    """
    n_samples = vectors.shape[0]
    if n_samples == 0:
        return ClusterAssignment(
            labels=np.zeros(0, dtype=int), method="none", n_clusters=0,
            outlier_scores=np.zeros(0, dtype=np.float32),
        )
    chosen, warnings = resolve_clusterer(method, n_samples)

    if chosen == "single":
        return ClusterAssignment(
            labels=np.zeros(n_samples, dtype=int), method="single", n_clusters=1,
            outlier_scores=np.zeros(n_samples, dtype=np.float32), warnings=warnings,
        )
    if chosen == "hdbscan":
        assignment = _cluster_hdbscan(vectors, min_cluster_size)
    elif chosen == "agglomerative":
        assignment = _cluster_agglomerative(vectors, n_clusters, min_cluster_size)
    else:
        assignment = _cluster_kmeans(vectors, n_clusters)

    assignment.warnings = [*warnings, *assignment.warnings]
    assignment.labels = relabel_by_size(assignment.labels)
    assignment.metrics.update(evaluate_clustering(vectors, assignment.labels))
    log.info(
        "clustered %s papers into %s clusters via %s",
        n_samples, assignment.n_clusters, assignment.method,
    )
    return assignment


def relabel_by_size(labels: np.ndarray) -> np.ndarray:
    """Renumber clusters so 0 is largest, 1 second largest, noise stays -1."""
    counts: dict[int, int] = {}
    for label in labels:
        value = int(label)
        if value == NOISE_LABEL:
            continue
        counts[value] = counts.get(value, 0) + 1
    ordered = sorted(counts, key=lambda label: (-counts[label], label))
    mapping = {old: new for new, old in enumerate(ordered)}
    mapping[NOISE_LABEL] = NOISE_LABEL
    return np.array([mapping.get(int(v), NOISE_LABEL) for v in labels], dtype=int)


def evaluate_clustering(vectors: np.ndarray, labels: np.ndarray) -> dict[str, Any]:
    """Quality metrics, surfaced so the user can judge the map.

    Silhouette is computed on non-noise points only - including noise would
    penalise HDBSCAN for correctly identifying outliers.
    """
    result: dict[str, Any] = {}
    mask = labels != NOISE_LABEL
    unique = {int(v) for v in labels[mask]}
    result["n_noise"] = int((~mask).sum())
    result["n_clustered"] = int(mask.sum())
    if len(unique) < 2 or mask.sum() < 3:
        return result
    try:
        from sklearn.metrics import (
            calinski_harabasz_score,
            davies_bouldin_score,
            silhouette_score,
        )

        subset, sub_labels = vectors[mask], labels[mask]
        result["silhouette"] = round(
            float(silhouette_score(subset, sub_labels, metric="cosine")), 4
        )
        result["davies_bouldin"] = round(
            float(davies_bouldin_score(subset, sub_labels)), 4
        )
        result["calinski_harabasz"] = round(
            float(calinski_harabasz_score(subset, sub_labels)), 2
        )
    except (ImportError, ValueError) as exc:
        log.debug("clustering metrics unavailable: %s", exc)
    return result


def cluster_centroids(vectors: np.ndarray, labels: np.ndarray) -> dict[int, np.ndarray]:
    """Mean vector per cluster, in embedding space (used for coherence + gaps)."""
    centroids: dict[int, np.ndarray] = {}
    for label in {int(v) for v in labels if v != NOISE_LABEL}:
        members = vectors[labels == label]
        if members.size:
            centroid = members.mean(axis=0)
            norm = np.linalg.norm(centroid) or 1.0
            centroids[label] = (centroid / norm).astype(np.float32)
    return centroids


def cluster_coherence(
    vectors: np.ndarray, labels: np.ndarray, centroids: dict[int, np.ndarray]
) -> dict[int, float]:
    """Mean cosine similarity of members to their centroid, per cluster.

    A low value flags a cluster the user should not trust as a single topic.
    """
    out: dict[int, float] = {}
    for label, centroid in centroids.items():
        members = vectors[labels == label]
        if members.size:
            out[label] = round(float(np.mean(members @ centroid)), 4)
    return out


def representative_papers(
    papers: list[Paper],
    vectors: np.ndarray,
    labels: np.ndarray,
    centroids: dict[int, np.ndarray],
    *,
    per_cluster: int = 3,
) -> dict[int, list[str]]:
    """The papers closest to each centroid - the cluster's exemplars.

    Ties are broken by citation count, so the exemplar shown to the user is both
    central *and* recognisable.
    """
    out: dict[int, list[str]] = {}
    for label, centroid in centroids.items():
        indices = np.flatnonzero(labels == label)
        if indices.size == 0:
            continue
        similarities = vectors[indices] @ centroid
        ranked = sorted(
            zip(indices.tolist(), similarities.tolist()),
            key=lambda pair: (-pair[1], -papers[pair[0]].citation_count),
        )
        out[label] = [papers[index].id for index, _ in ranked[:per_cluster]]
    return out


def describe_clusterers() -> list[dict[str, Any]]:
    return [
        {
            "id": "hdbscan",
            "name": "HDBSCAN",
            "available": hdbscan_available(),
            "auto_k": True,
            "detects_outliers": True,
            "requirement": 'pip install "papercreator[analysis]" or scikit-learn>=1.3',
            "note": "finds the cluster count itself and leaves genuine outliers "
                    "unassigned instead of distorting clusters",
        },
        {
            "id": "kmeans",
            "name": "KMeans",
            "available": True,
            "auto_k": False,
            "detects_outliers": False,
            "requirement": "included in the base install",
            "note": "fast and predictable; k is chosen by silhouette when not set",
        },
        {
            "id": "agglomerative",
            "name": "Agglomerative",
            "available": True,
            "auto_k": True,
            "detects_outliers": False,
            "requirement": "included in the base install",
            "note": "hierarchical; splits until clusters are within a distance "
                    "threshold",
        },
    ]

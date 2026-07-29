"""Analysis and embedding persistence.

Two responsibilities:

**Embedding cache** (``embeddings`` table) - vectors are expensive (a local
transformer takes ~10ms/paper, an API embedding costs money), so they are cached
per ``(paper_id, model)`` and invalidated by a hash of the embedded text. Stored
as raw float32 bytes rather than JSON: 384 floats are 1.5 KB as a BLOB versus
~9 KB as JSON text, and ``numpy.frombuffer`` reads them with no parsing.

**Analysis snapshots** (``analyses`` + ``analysis_points``) - a materialised
landscape. The fitted reducer is pickled into ``analyses.projector`` so a paper
added later can be placed into the *same* coordinate space instead of forcing a
full recompute that would move every existing point.
"""

from __future__ import annotations

import pickle
from typing import Any

from ..core.db import dumps, execute, loads, query, query_one, transaction
from ..core.errors import NotFoundError
from ..core.logging_setup import get_logger
from ..core.models import (
    AnalysisConfig,
    AnalysisResult,
    ClusterInfo,
    GapCandidate,
    HeatmapData,
    KeywordStat,
    PaperPoint,
)
from ..core.util import new_id, sha256_text, utc_now_iso

log = get_logger(__name__)

# ------------------------------------------------------------------ embeddings


def embedding_text_hash(text: str) -> str:
    return sha256_text(text)[:32]


def get_embedding(paper_id: str, model: str, text_hash: str = "") -> bytes | None:
    """Cached vector bytes, or ``None`` when absent or stale.

    Passing ``text_hash`` enables staleness detection: if the paper's title or
    abstract changed since the vector was computed, the cache misses.
    """
    row = query_one(
        "SELECT vector, text_hash FROM embeddings WHERE paper_id=? AND model=?",
        (paper_id, model),
    )
    if row is None:
        return None
    if text_hash and row["text_hash"] != text_hash:
        return None
    return bytes(row["vector"])


def get_embeddings_bulk(
    paper_ids: list[str], model: str
) -> dict[str, tuple[bytes, str]]:
    """Batch read: ``{paper_id: (vector_bytes, text_hash)}``."""
    if not paper_ids:
        return {}
    out: dict[str, tuple[bytes, str]] = {}
    for i in range(0, len(paper_ids), 400):
        chunk = paper_ids[i: i + 400]
        placeholders = ",".join("?" * len(chunk))
        rows = query(
            f"SELECT paper_id, vector, text_hash FROM embeddings"
            f" WHERE model=? AND paper_id IN ({placeholders})",
            (model, *chunk),
        )
        for row in rows:
            out[row["paper_id"]] = (bytes(row["vector"]), row["text_hash"] or "")
    return out


def put_embedding(
    paper_id: str, model: str, vector: bytes, dim: int, text_hash: str
) -> None:
    execute(
        "INSERT INTO embeddings (paper_id, model, dim, vector, text_hash, created_at)"
        " VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(paper_id, model) DO UPDATE SET vector=excluded.vector,"
        " dim=excluded.dim, text_hash=excluded.text_hash,"
        " created_at=excluded.created_at",
        (paper_id, model, dim, vector, text_hash, utc_now_iso()),
    )


def put_embeddings_bulk(items: list[tuple[str, str, bytes, int, str]]) -> int:
    """Cache a batch of vectors in one transaction. Returns how many were stored.

    Papers not present in the ``papers`` table are skipped rather than attempted.
    ``embeddings.paper_id`` carries a foreign key, and embedding a paper that has
    not been persisted is legitimate - placing an unsaved idea, previewing search
    results, analysing transient records. Left unfiltered the whole batch fails
    with "FOREIGN KEY constraint failed", and because callers treat any cache
    error as a backend failure, the semantic backend would silently degrade to
    TF-IDF. Caching is an optimisation: a missing paper must cost the cache entry
    and nothing else.
    """
    if not items:
        return 0
    ids = sorted({item[0] for item in items})
    known: set[str] = set()
    for start in range(0, len(ids), 400):
        chunk = ids[start: start + 400]
        placeholders = ",".join("?" * len(chunk))
        known.update(
            row["id"]
            for row in query(f"SELECT id FROM papers WHERE id IN ({placeholders})", chunk)
        )
    storable = [item for item in items if item[0] in known]
    if len(storable) != len(items):
        log.debug(
            "not caching %s embedding(s): those papers are not in the library yet",
            len(items) - len(storable),
        )
    if not storable:
        return 0

    now = utc_now_iso()
    with transaction():
        for paper_id, model, vector, dim, text_hash in storable:
            # Column order is (paper_id, model, dim, vector, ...) - vector and dim
            # must not be transposed here.
            execute(
                "INSERT INTO embeddings (paper_id, model, dim, vector, text_hash,"
                " created_at) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(paper_id, model) DO UPDATE SET vector=excluded.vector,"
                " dim=excluded.dim, text_hash=excluded.text_hash,"
                " created_at=excluded.created_at",
                (paper_id, model, dim, vector, text_hash, now),
            )
    return len(storable)


def embedding_stats() -> list[dict[str, Any]]:
    rows = query(
        "SELECT model, COUNT(*) AS n, MAX(dim) AS dim FROM embeddings GROUP BY model"
    )
    return [{"model": r["model"], "count": r["n"], "dim": r["dim"]} for r in rows]


def clear_embeddings(model: str = "") -> int:
    cur = (
        execute("DELETE FROM embeddings WHERE model=?", (model,))
        if model
        else execute("DELETE FROM embeddings")
    )
    return cur.rowcount or 0


# -------------------------------------------------------------------- analyses


def save_analysis(
    result: AnalysisResult, *, projector: Any = None, paper_ids: list[str] | None = None
) -> AnalysisResult:
    """Persist a landscape. ``projector`` is pickled for incremental placement.

    Pickle is acceptable here because the blob is written and read by this same
    application from a local file it owns - it is never accepted from a remote
    source. :func:`load_projector` still guards against a version mismatch.
    """
    aid = result.id or new_id("ana")
    result.id = aid
    now = utc_now_iso()
    blob: bytes | None = None
    if projector is not None:
        try:
            blob = pickle.dumps(projector, protocol=pickle.HIGHEST_PROTOCOL)
        except (pickle.PicklingError, TypeError, AttributeError) as exc:
            # Non-fatal: the landscape is still valid, only incremental
            # placement will need a refit.
            log.warning("could not pickle projector for analysis %s: %s", aid, exc)

    ids = paper_ids if paper_ids is not None else [p.paper_id for p in result.points]
    with transaction():
        execute(
            "INSERT INTO analyses (id, project_id, name, paper_ids, config,"
            " embedding_model, reducer, clusterer, n_papers, n_clusters, metrics,"
            " clusters, gaps, keywords, heatmap, projector, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name,"
            " paper_ids=excluded.paper_ids, config=excluded.config,"
            " embedding_model=excluded.embedding_model, reducer=excluded.reducer,"
            " clusterer=excluded.clusterer, n_papers=excluded.n_papers,"
            " n_clusters=excluded.n_clusters, metrics=excluded.metrics,"
            " clusters=excluded.clusters, gaps=excluded.gaps,"
            " keywords=excluded.keywords, heatmap=excluded.heatmap,"
            " projector=excluded.projector, updated_at=excluded.updated_at",
            (aid, result.project_id or None, result.name, dumps(ids),
             dumps(result.config.model_dump()), result.embedding_model,
             result.reducer, result.clusterer, result.n_papers, result.n_clusters,
             dumps(result.metrics),
             dumps([c.model_dump() for c in result.clusters]),
             dumps([g.model_dump() for g in result.gaps]),
             dumps([k.model_dump() for k in result.keywords]),
             dumps(result.heatmap.model_dump()), blob,
             result.created_at or now, now),
        )
        execute("DELETE FROM analysis_points WHERE analysis_id=?", (aid,))
        for point in result.points:
            execute(
                "INSERT INTO analysis_points (analysis_id, paper_id, x, y, z,"
                " cluster, outlier, is_seed, density) VALUES (?,?,?,?,?,?,?,?,?)",
                (aid, point.paper_id, point.x, point.y, point.z, point.cluster,
                 point.outlier, int(point.is_seed), point.density),
            )
    result.created_at = result.created_at or now
    result.updated_at = now
    return result


def get_analysis(analysis_id: str, *, with_points: bool = True) -> AnalysisResult | None:
    row = query_one("SELECT * FROM analyses WHERE id=?", (analysis_id,))
    if row is None:
        return None
    data = dict(row)
    result = AnalysisResult(
        id=data["id"],
        project_id=data.get("project_id") or "",
        name=data.get("name") or "",
        config=AnalysisConfig.model_validate(loads(data.get("config"), {}) or {}),
        embedding_model=data.get("embedding_model") or "",
        reducer=data.get("reducer") or "",
        clusterer=data.get("clusterer") or "",
        clusters=[ClusterInfo.model_validate(c)
                  for c in loads(data.get("clusters"), []) or []],
        gaps=[GapCandidate.model_validate(g)
              for g in loads(data.get("gaps"), []) or []],
        keywords=[KeywordStat.model_validate(k)
                  for k in loads(data.get("keywords"), []) or []],
        heatmap=HeatmapData.model_validate(loads(data.get("heatmap"), {}) or {}),
        metrics=loads(data.get("metrics"), {}) or {},
        n_papers=int(data.get("n_papers") or 0),
        n_clusters=int(data.get("n_clusters") or 0),
        created_at=data.get("created_at") or "",
        updated_at=data.get("updated_at") or "",
    )
    if with_points:
        result.points = [
            PaperPoint(
                paper_id=r["paper_id"], x=r["x"], y=r["y"], z=r["z"],
                cluster=r["cluster"], outlier=r["outlier"],
                is_seed=bool(r["is_seed"]), density=r["density"],
            )
            for r in query(
                "SELECT * FROM analysis_points WHERE analysis_id=? ORDER BY rowid",
                (analysis_id,),
            )
        ]
    return result


def require_analysis(analysis_id: str) -> AnalysisResult:
    result = get_analysis(analysis_id)
    if result is None:
        raise NotFoundError(f"analysis {analysis_id} not found")
    return result


def analysis_paper_ids(analysis_id: str) -> list[str]:
    row = query_one("SELECT paper_ids FROM analyses WHERE id=?", (analysis_id,))
    return (loads(row["paper_ids"], []) or []) if row else []


def load_projector(analysis_id: str) -> Any | None:
    """Unpickle the stored reducer, or ``None`` if absent/incompatible.

    A library upgrade (new umap-learn) can make an old pickle unloadable. That
    is expected and recoverable, so it is logged and treated as a cache miss.
    """
    row = query_one("SELECT projector FROM analyses WHERE id=?", (analysis_id,))
    if row is None or row["projector"] is None:
        return None
    try:
        return pickle.loads(bytes(row["projector"]))
    except Exception as exc:  # noqa: BLE001 - any unpickle failure is a cache miss
        log.warning(
            "stored projector for analysis %s is unreadable (%s); "
            "incremental placement will fall back to nearest-neighbour",
            analysis_id, exc,
        )
        return None


def list_analyses(project_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """Summaries only - points and heatmaps are too big for a list view."""
    if project_id:
        rows = query(
            "SELECT id, project_id, name, embedding_model, reducer, clusterer,"
            " n_papers, n_clusters, metrics, created_at, updated_at FROM analyses"
            " WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        )
    else:
        rows = query(
            "SELECT id, project_id, name, embedding_model, reducer, clusterer,"
            " n_papers, n_clusters, metrics, created_at, updated_at FROM analyses"
            " ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    out = []
    for row in rows:
        item = dict(row)
        item["metrics"] = loads(item.get("metrics"), {}) or {}
        out.append(item)
    return out


def latest_analysis_id(project_id: str) -> str:
    row = query_one(
        "SELECT id FROM analyses WHERE project_id=? ORDER BY created_at DESC LIMIT 1",
        (project_id,),
    )
    return row["id"] if row else ""


def delete_analysis(analysis_id: str) -> bool:
    cur = execute("DELETE FROM analyses WHERE id=?", (analysis_id,))
    return bool(cur.rowcount)


def add_points(analysis_id: str, points: list[PaperPoint]) -> int:
    """Append points to an existing landscape (incremental add).

    Also refreshes ``paper_ids`` and ``n_papers`` so the stored input set stays
    consistent with the points table.
    """
    if not points:
        return 0
    existing = analysis_paper_ids(analysis_id)
    with transaction():
        for point in points:
            execute(
                "INSERT INTO analysis_points (analysis_id, paper_id, x, y, z,"
                " cluster, outlier, is_seed, density) VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(analysis_id, paper_id) DO UPDATE SET x=excluded.x,"
                " y=excluded.y, z=excluded.z, cluster=excluded.cluster,"
                " outlier=excluded.outlier, is_seed=excluded.is_seed,"
                " density=excluded.density",
                (analysis_id, point.paper_id, point.x, point.y, point.z,
                 point.cluster, point.outlier, int(point.is_seed), point.density),
            )
        merged = list(dict.fromkeys([*existing, *(p.paper_id for p in points)]))
        execute(
            "UPDATE analyses SET paper_ids=?, n_papers=?, updated_at=? WHERE id=?",
            (dumps(merged), len(merged), utc_now_iso(), analysis_id),
        )
    return len(points)


def remove_points(analysis_id: str, paper_ids: list[str]) -> int:
    """Remove points from a landscape without recomputing it."""
    if not paper_ids:
        return 0
    placeholders = ",".join("?" * len(paper_ids))
    with transaction():
        cur = execute(
            f"DELETE FROM analysis_points WHERE analysis_id=?"
            f" AND paper_id IN ({placeholders})",
            (analysis_id, *paper_ids),
        )
        remaining = [
            pid for pid in analysis_paper_ids(analysis_id) if pid not in set(paper_ids)
        ]
        execute(
            "UPDATE analyses SET paper_ids=?, n_papers=?, updated_at=? WHERE id=?",
            (dumps(remaining), len(remaining), utc_now_iso(), analysis_id),
        )
    return cur.rowcount or 0

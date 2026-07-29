"""Paper library repository.

Owns the ``papers``, ``papers_fts``, ``collections``, ``collection_items``,
``searches`` and ``search_results`` tables.

Key behaviour: :func:`upsert` is a *merge*, not a replace. The same work
arriving from arXiv (rich abstract, no citations) and OpenAlex (citations,
truncated abstract) must end up as one row holding the best of both, without
ever discarding user-owned fields (notes, rating, tags, read_status).
"""

from __future__ import annotations

from typing import Any

from ..core.db import (
    dumps,
    execute,
    loads,
    query,
    query_one,
    row_to_dict,
    transaction,
)
from ..core.errors import NotFoundError
from ..core.logging_setup import get_logger
from ..core.models import Author, Paper
from ..core.util import dedupe_preserving_order, new_id, utc_now_iso

log = get_logger(__name__)

_PAPER_COLUMNS = (
    "id", "title", "abstract", "authors", "year", "venue", "venue_type", "doi",
    "arxiv_id", "pmid", "openalex_id", "s2_id", "url", "pdf_url", "pdf_path",
    "is_open_access", "citation_count", "reference_count", "fields_of_study",
    "keywords", "references_ids", "language", "source_providers", "raw",
    "origin", "notes", "rating", "read_status", "tags", "created_at",
    "updated_at",
)

# Fields the user owns. A re-import from a provider must never touch these.
_USER_FIELDS = ("notes", "rating", "read_status", "tags", "origin")


def row_to_paper(row: Any) -> Paper:
    data = dict(row)
    return Paper(
        id=data["id"],
        title=data.get("title") or "",
        abstract=data.get("abstract") or "",
        authors=[Author(**a) if isinstance(a, dict) else Author(name=str(a))
                 for a in loads(data.get("authors"), []) or []],
        year=data.get("year"),
        venue=data.get("venue") or "",
        venue_type=data.get("venue_type") or "",
        doi=data.get("doi") or "",
        arxiv_id=data.get("arxiv_id") or "",
        pmid=data.get("pmid") or "",
        openalex_id=data.get("openalex_id") or "",
        s2_id=data.get("s2_id") or "",
        url=data.get("url") or "",
        pdf_url=data.get("pdf_url") or "",
        pdf_path=data.get("pdf_path") or "",
        is_open_access=bool(data.get("is_open_access")),
        citation_count=data.get("citation_count") or 0,
        reference_count=data.get("reference_count") or 0,
        fields_of_study=loads(data.get("fields_of_study"), []) or [],
        keywords=loads(data.get("keywords"), []) or [],
        references_ids=loads(data.get("references_ids"), []) or [],
        language=data.get("language") or "",
        source_providers=loads(data.get("source_providers"), []) or [],
        raw=loads(data.get("raw"), {}) or {},
        origin=data.get("origin") or "retrieved",
        notes=data.get("notes") or "",
        rating=data.get("rating") or 0,
        read_status=data.get("read_status") or "unread",
        tags=loads(data.get("tags"), []) or [],
        created_at=data.get("created_at") or "",
        updated_at=data.get("updated_at") or "",
    )


def _paper_to_params(paper: Paper) -> dict[str, Any]:
    return {
        "id": paper.id,
        "title": paper.title,
        "abstract": paper.abstract,
        "authors": dumps([a.model_dump() for a in paper.authors]),
        "year": paper.year,
        "venue": paper.venue,
        "venue_type": paper.venue_type,
        "doi": paper.doi,
        "arxiv_id": paper.arxiv_id,
        "pmid": paper.pmid,
        "openalex_id": paper.openalex_id,
        "s2_id": paper.s2_id,
        "url": paper.url,
        "pdf_url": paper.pdf_url,
        "pdf_path": paper.pdf_path,
        "is_open_access": int(paper.is_open_access),
        "citation_count": paper.citation_count,
        "reference_count": paper.reference_count,
        "fields_of_study": dumps(paper.fields_of_study),
        "keywords": dumps(paper.keywords),
        "references_ids": dumps(paper.references_ids),
        "language": paper.language,
        "source_providers": dumps(paper.source_providers),
        "raw": dumps(paper.raw),
        "origin": paper.origin,
        "notes": paper.notes,
        "rating": paper.rating,
        "read_status": paper.read_status,
        "tags": dumps(paper.tags),
    }


def merge_papers(existing: Paper, incoming: Paper) -> Paper:
    """Field-wise merge, ``existing`` wins for user data.

    Rules, in priority order:

    * user fields (:data:`_USER_FIELDS`) always keep the existing value;
    * longer text wins for abstract (providers truncate differently);
    * non-empty wins for identifiers and URLs (fills gaps);
    * max wins for counts (a stale provider may report 0 citations);
    * lists are unioned, order-preserving.
    """
    merged = existing.model_copy(deep=True)

    if len(incoming.abstract) > len(merged.abstract):
        merged.abstract = incoming.abstract
    if len(incoming.title) > len(merged.title) and not merged.title:
        merged.title = incoming.title
    if len(incoming.authors) > len(merged.authors):
        merged.authors = incoming.authors
    elif len(incoming.authors) == len(merged.authors):
        # Same count: prefer the version carrying affiliations/ORCIDs.
        if sum(bool(a.affiliation or a.orcid) for a in incoming.authors) > sum(
            bool(a.affiliation or a.orcid) for a in merged.authors
        ):
            merged.authors = incoming.authors

    for field in ("venue", "venue_type", "doi", "arxiv_id", "pmid",
                  "openalex_id", "s2_id", "url", "pdf_url", "language"):
        if not getattr(merged, field) and getattr(incoming, field):
            setattr(merged, field, getattr(incoming, field))

    # Year needs its own rule. Providers disagree, sometimes wildly: OpenAlex
    # carries the 2017 transformer paper with publication_year 2025 (a DOI
    # re-registration). When two sources differ by more than the normal
    # preprint/publication gap, prefer the EARLIER year - a too-late year is the
    # common failure mode (re-registration, indexing date leaking into the
    # publication date), whereas a too-early one is rare. The disagreement is
    # recorded so the UI can flag it rather than hiding the conflict.
    if merged.year is None:
        merged.year = incoming.year
    elif incoming.year is not None and incoming.year != merged.year:
        if abs(incoming.year - merged.year) > 1:
            conflict = sorted({merged.year, incoming.year})
            merged.raw.setdefault("conflicts", {})["year"] = conflict
            merged.year = conflict[0]
        else:
            # Within one year: keep the existing value, it is not worth churning.
            pass

    merged.is_open_access = merged.is_open_access or incoming.is_open_access
    merged.citation_count = max(merged.citation_count, incoming.citation_count)
    merged.reference_count = max(merged.reference_count, incoming.reference_count)

    merged.keywords = dedupe_preserving_order([*merged.keywords, *incoming.keywords])
    merged.fields_of_study = dedupe_preserving_order(
        [*merged.fields_of_study, *incoming.fields_of_study]
    )
    merged.references_ids = dedupe_preserving_order(
        [*merged.references_ids, *incoming.references_ids]
    )
    merged.source_providers = dedupe_preserving_order(
        [*merged.source_providers, *incoming.source_providers]
    )
    merged.raw = {**incoming.raw, **merged.raw}

    # An 'idea' or 'own_paper' must not be demoted to 'retrieved' by a later
    # provider hit that happens to match it.
    if merged.origin == "retrieved" and incoming.origin != "retrieved":
        merged.origin = incoming.origin
    return merged


def get(paper_id: str) -> Paper | None:
    row = query_one("SELECT * FROM papers WHERE id=?", (paper_id,))
    return row_to_paper(row) if row else None


def require(paper_id: str) -> Paper:
    paper = get(paper_id)
    if paper is None:
        raise NotFoundError(f"paper {paper_id} not found")
    return paper


def get_many(paper_ids: list[str]) -> list[Paper]:
    """Fetch in the requested order. Missing ids are skipped silently."""
    if not paper_ids:
        return []
    found: dict[str, Paper] = {}
    # SQLite's default limit is 999 host parameters.
    for i in range(0, len(paper_ids), 500):
        chunk = paper_ids[i: i + 500]
        placeholders = ",".join("?" * len(chunk))
        for row in query(f"SELECT * FROM papers WHERE id IN ({placeholders})", chunk):
            paper = row_to_paper(row)
            found[paper.id] = paper
    return [found[pid] for pid in paper_ids if pid in found]


def find_existing(paper: Paper) -> Paper | None:
    """Locate an existing row for this work by any strong identifier.

    Title matching is deliberately *not* done here - that is the retrieval
    deduper's job, which has the whole candidate batch in hand and can apply a
    similarity threshold. This function only trusts exact identifiers.
    """
    row = query_one("SELECT * FROM papers WHERE id=?", (paper.id,)) if paper.id else None
    if row:
        return row_to_paper(row)
    for column, value in (
        ("doi", paper.doi), ("arxiv_id", paper.arxiv_id), ("pmid", paper.pmid),
        ("openalex_id", paper.openalex_id), ("s2_id", paper.s2_id),
    ):
        if value:
            row = query_one(f"SELECT * FROM papers WHERE {column}=?", (value,))
            if row:
                return row_to_paper(row)
    return None


def upsert(paper: Paper) -> Paper:
    """Insert or merge-update one paper. Returns the stored state."""
    paper.ensure_id()
    existing = find_existing(paper)
    now = utc_now_iso()
    if existing is not None:
        merged = merge_papers(existing, paper)
        merged.id = existing.id
        params = _paper_to_params(merged)
        assignments = ",".join(
            f"{col}=:{col}" for col in params if col not in ("id",)
        )
        execute(
            f"UPDATE papers SET {assignments}, updated_at=:updated_at WHERE id=:id",
            {**params, "updated_at": now},
        )
        merged.updated_at = now
        merged.created_at = existing.created_at
        return merged

    params = _paper_to_params(paper)
    columns = ",".join([*params.keys(), "created_at", "updated_at"])
    placeholders = ",".join([f":{k}" for k in params] + [":created_at", ":updated_at"])
    execute(
        f"INSERT INTO papers ({columns}) VALUES ({placeholders})",
        {**params, "created_at": now, "updated_at": now},
    )
    paper.created_at = paper.updated_at = now
    return paper


def upsert_many(papers: list[Paper]) -> tuple[list[Paper], int, int]:
    """Batch upsert inside one transaction.

    Returns ``(stored, inserted_count, updated_count)``. One transaction for the
    whole batch matters: a 300-paper search would otherwise pay 300 fsyncs.
    """
    stored: list[Paper] = []
    inserted = updated = 0
    with transaction():
        for paper in papers:
            paper.ensure_id()
            before = find_existing(paper)
            result = upsert(paper)
            stored.append(result)
            if before is None:
                inserted += 1
            else:
                updated += 1
    return stored, inserted, updated


def update_fields(paper_id: str, **fields: Any) -> Paper:
    """Patch specific columns. Used by the UI for notes/rating/tags/pdf_path."""
    if not fields:
        return require(paper_id)
    allowed = {
        "title", "abstract", "year", "venue", "venue_type", "doi", "url",
        "pdf_url", "pdf_path", "notes", "rating", "read_status", "origin",
        "language", "is_open_access", "citation_count",
    }
    json_fields = {"tags", "keywords", "fields_of_study"}
    assignments, params = [], {}
    for key, value in fields.items():
        if key in allowed:
            assignments.append(f"{key}=:{key}")
            params[key] = int(value) if key == "is_open_access" else value
        elif key in json_fields:
            assignments.append(f"{key}=:{key}")
            params[key] = dumps(value)
        elif key == "authors":
            assignments.append("authors=:authors")
            params["authors"] = dumps(
                [a if isinstance(a, dict) else {"name": str(a)} for a in value]
            )
    if not assignments:
        return require(paper_id)
    params["id"] = paper_id
    params["updated_at"] = utc_now_iso()
    execute(
        f"UPDATE papers SET {','.join(assignments)}, updated_at=:updated_at"
        " WHERE id=:id",
        params,
    )
    return require(paper_id)


def delete(paper_id: str) -> bool:
    """Remove a paper globally. Cascades to collections, embeddings, results.

    Analyses keep their ``analysis_points`` row (no FK to papers on purpose) so
    a saved landscape stays renderable; the UI shows such points as "removed".
    """
    cur = execute("DELETE FROM papers WHERE id=?", (paper_id,))
    return bool(cur.rowcount)


def delete_many(paper_ids: list[str]) -> int:
    if not paper_ids:
        return 0
    total = 0
    with transaction():
        for i in range(0, len(paper_ids), 500):
            chunk = paper_ids[i: i + 500]
            placeholders = ",".join("?" * len(chunk))
            cur = execute(f"DELETE FROM papers WHERE id IN ({placeholders})", chunk)
            total += cur.rowcount or 0
    return total


# ---------------------------------------------------------------- listing


def _fts_escape(text: str) -> str:
    """Quote a user string as a single FTS5 phrase.

    FTS5 treats ``-``, ``*``, ``:``, ``"`` and ``NEAR`` as syntax; a raw paper
    title routinely contains them and would raise a syntax error. Wrapping in
    double quotes (with internal quotes doubled) makes it a literal phrase.
    """
    cleaned = text.replace('"', '""').strip()
    return f'"{cleaned}"' if cleaned else '""'


def search_library(
    *,
    text: str = "",
    project_id: str = "",
    collection_id: str = "",
    year_from: int | None = None,
    year_to: int | None = None,
    origin: str = "",
    read_status: str = "",
    tag: str = "",
    min_rating: int = 0,
    open_access_only: bool = False,
    sort: str = "updated",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Filtered library listing. Returns ``{items, total, limit, offset}``.

    Full-text search runs through ``papers_fts`` when ``text`` is given, which
    also supplies the ``relevance`` sort via bm25.
    """
    joins, clauses, params = [], [], []
    if text.strip():
        joins.append("JOIN papers_fts ON papers_fts.rowid = papers.rowid")
        clauses.append("papers_fts MATCH ?")
        params.append(_fts_escape(text))
    if collection_id:
        joins.append("JOIN collection_items ci ON ci.paper_id = papers.id")
        clauses.append("ci.collection_id = ?")
        params.append(collection_id)
    elif project_id:
        joins.append(
            "JOIN collection_items ci ON ci.paper_id = papers.id "
            "JOIN collections c ON c.id = ci.collection_id"
        )
        clauses.append("c.project_id = ?")
        params.append(project_id)
    if year_from:
        clauses.append("papers.year >= ?")
        params.append(year_from)
    if year_to:
        clauses.append("papers.year <= ?")
        params.append(year_to)
    if origin:
        clauses.append("papers.origin = ?")
        params.append(origin)
    if read_status:
        clauses.append("papers.read_status = ?")
        params.append(read_status)
    if min_rating:
        clauses.append("papers.rating >= ?")
        params.append(min_rating)
    if open_access_only:
        clauses.append("papers.is_open_access = 1")
    if tag:
        # tags is a JSON array in TEXT; LIKE on the quoted form is exact enough
        # and avoids requiring the JSON1 extension.
        clauses.append("papers.tags LIKE ?")
        params.append(f'%"{tag}"%')

    join_sql = " ".join(dict.fromkeys(joins))
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = {
        "updated": "papers.updated_at DESC",
        "created": "papers.created_at DESC",
        "year": "papers.year DESC NULLS LAST",
        "citations": "papers.citation_count DESC",
        "title": "papers.title COLLATE NOCASE ASC",
        "rating": "papers.rating DESC, papers.updated_at DESC",
        "relevance": "bm25(papers_fts)" if text.strip() else "papers.updated_at DESC",
    }.get(sort, "papers.updated_at DESC")

    total_row = query_one(
        f"SELECT COUNT(DISTINCT papers.id) AS n FROM papers {join_sql} {where_sql}",
        params,
    )
    rows = query(
        f"SELECT DISTINCT papers.* FROM papers {join_sql} {where_sql} "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )
    return {
        "items": [row_to_paper(r).model_dump() for r in rows],
        "total": int(total_row["n"]) if total_row else 0,
        "limit": limit,
        "offset": offset,
    }


def library_stats() -> dict[str, Any]:
    """Counts for the library header and analysis preflight checks."""
    def scalar(sql: str, params: tuple = ()) -> int:
        row = query_one(sql, params)
        return int(row["n"]) if row and row["n"] is not None else 0

    years = query(
        "SELECT year, COUNT(*) AS n FROM papers WHERE year IS NOT NULL"
        " GROUP BY year ORDER BY year"
    )
    return {
        "total": scalar("SELECT COUNT(*) AS n FROM papers"),
        "with_abstract": scalar("SELECT COUNT(*) AS n FROM papers WHERE abstract != ''"),
        "open_access": scalar("SELECT COUNT(*) AS n FROM papers WHERE is_open_access=1"),
        "with_pdf": scalar("SELECT COUNT(*) AS n FROM papers WHERE pdf_path != ''"),
        "own": scalar(
            "SELECT COUNT(*) AS n FROM papers WHERE origin IN ('idea','own_paper')"
        ),
        "unread": scalar("SELECT COUNT(*) AS n FROM papers WHERE read_status='unread'"),
        "by_year": [{"year": r["year"], "count": r["n"]} for r in years],
    }


def all_tags() -> list[dict[str, Any]]:
    """Distinct tags with counts. Decoded in Python because tags are JSON TEXT."""
    counts: dict[str, int] = {}
    for row in query("SELECT tags FROM papers WHERE tags != '[]'"):
        for tag in loads(row["tags"], []) or []:
            counts[str(tag)] = counts.get(str(tag), 0) + 1
    return sorted(
        ({"tag": k, "count": v} for k, v in counts.items()),
        key=lambda d: (-d["count"], d["tag"]),
    )


# ------------------------------------------------------------- collections

DEFAULT_COLLECTION = "default"


def ensure_collection(
    project_id: str, name: str = DEFAULT_COLLECTION, kind: str = "manual"
) -> dict[str, Any]:
    """Get-or-create by ``(project_id, name)``. Safe to call on every write."""
    row = query_one(
        "SELECT * FROM collections WHERE project_id=? AND name=?", (project_id, name)
    )
    if row:
        return dict(row)
    cid = new_id("col")
    execute(
        "INSERT INTO collections (id, project_id, name, kind, created_at)"
        " VALUES (?,?,?,?,?)",
        (cid, project_id, name, kind, utc_now_iso()),
    )
    return dict(query_one("SELECT * FROM collections WHERE id=?", (cid,)))


def list_collections(project_id: str) -> list[dict[str, Any]]:
    rows = query(
        "SELECT c.*, (SELECT COUNT(*) FROM collection_items ci"
        " WHERE ci.collection_id = c.id) AS paper_count"
        " FROM collections c WHERE c.project_id=? ORDER BY c.created_at",
        (project_id,),
    )
    return [dict(r) for r in rows]


def add_to_collection(
    collection_id: str,
    paper_ids: list[str],
    *,
    scores: dict[str, float] | None = None,
) -> int:
    """Link papers to a collection. Idempotent; updates relevance on re-add."""
    if not paper_ids:
        return 0
    now = utc_now_iso()
    scores = scores or {}
    added = 0
    with transaction():
        for pid in paper_ids:
            cur = execute(
                "INSERT INTO collection_items (collection_id, paper_id, added_at,"
                " relevance) VALUES (?,?,?,?)"
                " ON CONFLICT(collection_id, paper_id) DO UPDATE SET"
                " relevance=MAX(relevance, excluded.relevance)",
                (collection_id, pid, now, float(scores.get(pid, 0.0))),
            )
            added += cur.rowcount or 0
    return added


def remove_from_collection(collection_id: str, paper_ids: list[str]) -> int:
    if not paper_ids:
        return 0
    placeholders = ",".join("?" * len(paper_ids))
    cur = execute(
        f"DELETE FROM collection_items WHERE collection_id=?"
        f" AND paper_id IN ({placeholders})",
        (collection_id, *paper_ids),
    )
    return cur.rowcount or 0


def delete_collection(collection_id: str) -> bool:
    cur = execute("DELETE FROM collections WHERE id=?", (collection_id,))
    return bool(cur.rowcount)


def collection_paper_ids(collection_id: str) -> list[str]:
    rows = query(
        "SELECT paper_id FROM collection_items WHERE collection_id=?"
        " ORDER BY relevance DESC, added_at",
        (collection_id,),
    )
    return [r["paper_id"] for r in rows]


def project_paper_ids(project_id: str) -> list[str]:
    """Every paper in any collection of the project, deduplicated."""
    rows = query(
        "SELECT DISTINCT ci.paper_id, MAX(ci.relevance) AS rel FROM collection_items ci"
        " JOIN collections c ON c.id = ci.collection_id WHERE c.project_id=?"
        " GROUP BY ci.paper_id ORDER BY rel DESC",
        (project_id,),
    )
    return [r["paper_id"] for r in rows]


def project_paper_count(project_id: str) -> int:
    row = query_one(
        "SELECT COUNT(DISTINCT ci.paper_id) AS n FROM collection_items ci"
        " JOIN collections c ON c.id = ci.collection_id WHERE c.project_id=?",
        (project_id,),
    )
    return int(row["n"]) if row else 0


# ---------------------------------------------------------- search history


def record_search(
    *,
    query_text: str,
    mode: str,
    seed_text: str,
    providers: list[str],
    params: dict[str, Any],
    papers: list[Paper],
    provider_stats: dict[str, Any],
    project_id: str = "",
) -> str:
    """Persist one search execution and its ranked result set."""
    sid = new_id("sr")
    with transaction():
        execute(
            "INSERT INTO searches (id, project_id, query, mode, seed_text, providers,"
            " params, result_count, provider_stats, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, project_id or None, query_text, mode, seed_text[:4000],
             dumps(providers), dumps(params), len(papers), dumps(provider_stats),
             utc_now_iso()),
        )
        for rank, paper in enumerate(papers):
            execute(
                "INSERT OR REPLACE INTO search_results (search_id, paper_id, rank,"
                " score, provider) VALUES (?,?,?,?,?)",
                (sid, paper.id, rank, float(paper.score),
                 ",".join(paper.source_providers[:3])),
            )
    return sid


def list_searches(project_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    if project_id:
        rows = query(
            "SELECT * FROM searches WHERE project_id=? ORDER BY created_at DESC"
            " LIMIT ?",
            (project_id, limit),
        )
    else:
        rows = query("SELECT * FROM searches ORDER BY created_at DESC LIMIT ?", (limit,))
    out = []
    for row in rows:
        item = dict(row)
        item["providers"] = loads(item.get("providers"), []) or []
        item["params"] = loads(item.get("params"), {}) or {}
        item["provider_stats"] = loads(item.get("provider_stats"), {}) or {}
        out.append(item)
    return out


def get_search(search_id: str) -> dict[str, Any] | None:
    row = row_to_dict(query_one("SELECT * FROM searches WHERE id=?", (search_id,)))
    if row is None:
        return None
    row["providers"] = loads(row.get("providers"), []) or []
    row["params"] = loads(row.get("params"), {}) or {}
    row["provider_stats"] = loads(row.get("provider_stats"), {}) or {}
    rows = query(
        "SELECT sr.paper_id, sr.rank, sr.score, sr.provider FROM search_results sr"
        " WHERE sr.search_id=? ORDER BY sr.rank",
        (search_id,),
    )
    row["results"] = [dict(r) for r in rows]
    return row


def delete_search(search_id: str) -> bool:
    cur = execute("DELETE FROM searches WHERE id=?", (search_id,))
    return bool(cur.rowcount)

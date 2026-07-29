"""SQLite access layer and schema migrations.

Why SQLite and not a server database: this is a single-user desktop app, the
data is a few hundred thousand paper records at most, and the file must be
trivially backup-able and inspectable with any SQLite browser.

Concurrency model
-----------------
FastAPI runs request handlers on a thread pool, and background jobs run on
their own threads, so connections are **thread-local** (:func:`connect`). WAL
journaling lets readers proceed during a write; ``busy_timeout`` absorbs the
brief write-lock contention that remains. A single process is assumed - two
PaperCreator instances against one database file is not supported and the
launcher prevents it by binding a fixed port.

Migrations
----------
:data:`MIGRATIONS` is an append-only list of ``(version, sql)``. On every
startup :func:`init_db` applies everything newer than ``PRAGMA user_version``
inside one transaction per step. Never edit a shipped migration - add a new
one, or existing installs silently diverge from new ones.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .logging_setup import get_logger
from .paths import get_paths

log = get_logger(__name__)

_local = threading.local()
_db_path_override: Path | None = None
_init_lock = threading.Lock()
_initialised_for: Path | None = None


def set_db_path(path: Path | None) -> None:
    """Point the layer at a different file (tests). Closes existing handles."""
    global _db_path_override, _initialised_for
    close_connection()
    _db_path_override = path
    _initialised_for = None


def db_path() -> Path:
    return _db_path_override or get_paths().db_file


def connect() -> sqlite3.Connection:
    """Thread-local connection with the pragmas this app depends on."""
    existing: sqlite3.Connection | None = getattr(_local, "conn", None)
    existing_path: Path | None = getattr(_local, "path", None)
    target = db_path()
    if existing is not None and existing_path == target:
        return existing
    if existing is not None:
        existing.close()

    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(target),
        timeout=30.0,
        # Handlers may hop threads within a request; we serialise with the
        # thread-local pattern plus SQLite's own locking.
        check_same_thread=False,
        isolation_level=None,  # explicit transactions via `transaction()`
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA temp_store=MEMORY")
    _local.conn = conn
    _local.path = target
    return conn


def close_connection() -> None:
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        _local.conn = None
        _local.path = None


def checkpoint_wal() -> dict[str, int]:
    """Checkpoint and truncate WAL before a graceful desktop shutdown.

    The return values mirror SQLite's ``wal_checkpoint`` pragma.  ``busy`` is
    non-zero when another connection still owns a transaction; callers should
    log that condition but must not delete or rewrite database files.
    """
    row = connect().execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    values = tuple(row) if row is not None else (0, 0, 0)
    return {
        "busy": int(values[0]),
        "log_frames": int(values[1]),
        "checkpointed_frames": int(values[2]),
    }


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT / ROLLBACK.

    IMMEDIATE (not DEFERRED) acquires the write lock up front, which turns
    "database is locked" mid-transaction failures into a clean wait bounded by
    ``busy_timeout``.
    """
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _bind(params: Iterable[Any] | Mapping[str, Any]) -> Any:
    """Normalise bind parameters.

    Mappings are passed through untouched for ``:name`` placeholders; anything
    else becomes a tuple for ``?`` placeholders. Coercing a dict with
    ``tuple()`` would silently bind its *keys*, which is a data-corrupting bug
    rather than an error on some CPython versions.
    """
    if isinstance(params, Mapping):
        return params
    return tuple(params)


def query(sql: str, params: Iterable[Any] | Mapping[str, Any] = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, _bind(params)).fetchall()


def query_one(
    sql: str, params: Iterable[Any] | Mapping[str, Any] = ()
) -> sqlite3.Row | None:
    return connect().execute(sql, _bind(params)).fetchone()


def execute(
    sql: str, params: Iterable[Any] | Mapping[str, Any] = ()
) -> sqlite3.Cursor:
    return connect().execute(sql, _bind(params))


def executemany(sql: str, seq: Iterable[Iterable[Any]]) -> sqlite3.Cursor:
    return connect().executemany(sql, [tuple(p) for p in seq])


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


def dumps(value: Any) -> str:
    """JSON for a TEXT column. Used for every list/dict-valued field."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: Any, default: Any = None) -> Any:
    """Tolerant JSON read - a hand-edited or truncated cell returns ``default``
    instead of raising, because a single bad row should not break a list view.
    """
    if value in (None, "", b""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_V1 = """
-- Global paper library. One row per deduplicated work, shared by all
-- projects; project membership lives in collection_items.
CREATE TABLE IF NOT EXISTS papers (
    id                TEXT PRIMARY KEY,          -- internal stable id (see store/papers.py)
    title             TEXT NOT NULL,
    abstract          TEXT DEFAULT '',
    authors           TEXT DEFAULT '[]',         -- JSON [{name, affiliation, orcid}]
    year              INTEGER,
    venue             TEXT DEFAULT '',
    venue_type        TEXT DEFAULT '',           -- journal|conference|preprint|book|thesis
    doi               TEXT,
    arxiv_id          TEXT,
    pmid              TEXT,
    openalex_id       TEXT,
    s2_id             TEXT,
    url               TEXT DEFAULT '',
    pdf_url           TEXT DEFAULT '',
    pdf_path          TEXT DEFAULT '',           -- local file once downloaded
    is_open_access    INTEGER DEFAULT 0,
    citation_count    INTEGER DEFAULT 0,
    reference_count   INTEGER DEFAULT 0,
    fields_of_study   TEXT DEFAULT '[]',         -- JSON [str]
    keywords          TEXT DEFAULT '[]',         -- JSON [str]
    references_ids    TEXT DEFAULT '[]',         -- JSON [external ids]
    language          TEXT DEFAULT '',
    source_providers  TEXT DEFAULT '[]',         -- JSON [provider_id] that returned it
    raw               TEXT DEFAULT '{}',         -- JSON provider payloads, for re-parsing
    origin            TEXT DEFAULT 'retrieved',  -- retrieved|manual|idea|own_paper
    notes             TEXT DEFAULT '',
    rating            INTEGER DEFAULT 0,         -- user triage 0-5
    read_status       TEXT DEFAULT 'unread',     -- unread|skimmed|read
    tags              TEXT DEFAULT '[]',         -- JSON [str]
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_papers_doi        ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_arxiv      ON papers(arxiv_id);
CREATE INDEX IF NOT EXISTS idx_papers_year       ON papers(year);
CREATE INDEX IF NOT EXISTS idx_papers_origin     ON papers(origin);
CREATE INDEX IF NOT EXISTS idx_papers_title_norm ON papers(title);

-- Full-text index over title+abstract+keywords for the library search box.
-- Kept in sync by triggers so store code never has to remember.
CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title, abstract, keywords, content='papers', content_rowid='rowid'
);
CREATE TRIGGER IF NOT EXISTS papers_fts_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, abstract, keywords)
    VALUES (new.rowid, new.title, new.abstract, new.keywords);
END;
CREATE TRIGGER IF NOT EXISTS papers_fts_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, keywords)
    VALUES ('delete', old.rowid, old.title, old.abstract, old.keywords);
END;
CREATE TRIGGER IF NOT EXISTS papers_fts_au AFTER UPDATE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, abstract, keywords)
    VALUES ('delete', old.rowid, old.title, old.abstract, old.keywords);
    INSERT INTO papers_fts(rowid, title, abstract, keywords)
    VALUES (new.rowid, new.title, new.abstract, new.keywords);
END;

-- A paper project == one workspace directory == one git repo.
CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,          -- directory name under workspace
    title         TEXT NOT NULL,
    title_zh      TEXT DEFAULT '',
    description   TEXT DEFAULT '',
    idea          TEXT DEFAULT '',               -- the user's seed idea (drives search + gap analysis)
    research_field TEXT DEFAULT '',
    target_venue  TEXT DEFAULT '',
    template_id   TEXT DEFAULT 'generic',
    language      TEXT DEFAULT 'en',
    bilingual     INTEGER DEFAULT 1,
    citation_style TEXT DEFAULT 'ieee',
    path          TEXT NOT NULL,                 -- absolute; may be relocated by user
    git_enabled   INTEGER DEFAULT 1,
    status        TEXT DEFAULT 'active',         -- active|archived
    settings      TEXT DEFAULT '{}',             -- JSON per-project overrides
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- Named sets of papers inside a project (search results, reading list,
-- "related work pool"). Every project gets a 'default' collection.
CREATE TABLE IF NOT EXISTS collections (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    kind        TEXT DEFAULT 'manual',           -- manual|search|imported
    description TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE(project_id, name)
);
CREATE TABLE IF NOT EXISTS collection_items (
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    paper_id      TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    added_at      TEXT NOT NULL,
    relevance     REAL DEFAULT 0,                -- provider/rerank score, 0-1
    note          TEXT DEFAULT '',
    PRIMARY KEY (collection_id, paper_id)
);
CREATE INDEX IF NOT EXISTS idx_ci_paper ON collection_items(paper_id);

-- One saved search execution: the query, which providers ran, what came back.
CREATE TABLE IF NOT EXISTS searches (
    id            TEXT PRIMARY KEY,
    project_id    TEXT REFERENCES projects(id) ON DELETE SET NULL,
    query         TEXT NOT NULL,
    mode          TEXT DEFAULT 'keyword',        -- keyword|idea|paper|advanced
    seed_text     TEXT DEFAULT '',               -- idea / abstract used for semantic modes
    providers     TEXT DEFAULT '[]',             -- JSON [provider_id]
    params        TEXT DEFAULT '{}',             -- JSON resolved SearchRequest
    result_count  INTEGER DEFAULT 0,
    provider_stats TEXT DEFAULT '{}',            -- JSON {provider: {count, ms, error}}
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_searches_project ON searches(project_id);
CREATE TABLE IF NOT EXISTS search_results (
    search_id  TEXT NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    paper_id   TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    rank       INTEGER DEFAULT 0,
    score      REAL DEFAULT 0,
    provider   TEXT DEFAULT '',
    PRIMARY KEY (search_id, paper_id)
);

-- Cached embedding vectors. Keyed by (model, content hash) so switching
-- models does not invalidate the other model's cache.
CREATE TABLE IF NOT EXISTS embeddings (
    paper_id   TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,                    -- float32 little-endian
    text_hash  TEXT NOT NULL,                    -- invalidates on title/abstract edit
    created_at TEXT NOT NULL,
    PRIMARY KEY (paper_id, model)
);

-- A materialised landscape: reduction + clustering + keywords + gaps for one
-- set of papers. Coordinates live in analysis_points so they can be paged.
CREATE TABLE IF NOT EXISTS analyses (
    id            TEXT PRIMARY KEY,
    project_id    TEXT REFERENCES projects(id) ON DELETE CASCADE,
    name          TEXT DEFAULT '',
    paper_ids     TEXT DEFAULT '[]',             -- JSON, input set (order = point order)
    config        TEXT DEFAULT '{}',             -- JSON AnalysisConfig actually used
    embedding_model TEXT DEFAULT '',
    reducer       TEXT DEFAULT '',
    clusterer     TEXT DEFAULT '',
    n_papers      INTEGER DEFAULT 0,
    n_clusters    INTEGER DEFAULT 0,
    metrics       TEXT DEFAULT '{}',             -- JSON silhouette/trustworthiness/etc
    clusters      TEXT DEFAULT '[]',             -- JSON [{id,label,keywords,centroid,...}]
    gaps          TEXT DEFAULT '[]',             -- JSON [{...gap candidates}]
    keywords      TEXT DEFAULT '[]',             -- JSON global keyword stats
    heatmap       TEXT DEFAULT '{}',             -- JSON density grid
    -- Fitted reducer state, so a new paper can be projected into the SAME
    -- space later without recomputing (see analysis/incremental.py).
    projector     BLOB,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_project ON analyses(project_id);
CREATE TABLE IF NOT EXISTS analysis_points (
    analysis_id TEXT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    paper_id    TEXT NOT NULL,
    x REAL, y REAL, z REAL,
    cluster     INTEGER DEFAULT -1,              -- -1 = noise/unassigned
    outlier     REAL DEFAULT 0,                  -- HDBSCAN outlier score
    is_seed     INTEGER DEFAULT 0,               -- user's own idea/paper marker
    density     REAL DEFAULT 0,
    PRIMARY KEY (analysis_id, paper_id)
);

-- Manuscript structure. Body text lives on disk (git-tracked); this table is
-- the index + metadata. See store/documents.py for the disk<->db contract.
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind        TEXT DEFAULT 'manuscript',       -- manuscript|note|outline|review
    title       TEXT DEFAULT '',
    format      TEXT DEFAULT 'markdown',         -- markdown|latex
    rel_path    TEXT NOT NULL,                   -- relative to project dir
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(project_id, rel_path)
);
CREATE TABLE IF NOT EXISTS sections (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parent_id   TEXT REFERENCES sections(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,                   -- stable slug: abstract, intro, method...
    title       TEXT DEFAULT '',
    title_zh    TEXT DEFAULT '',
    ordering    INTEGER DEFAULT 0,
    level       INTEGER DEFAULT 1,
    content     TEXT DEFAULT '',                 -- primary language body
    content_zh  TEXT DEFAULT '',                 -- paired translation (bilingual mode)
    status      TEXT DEFAULT 'empty',            -- empty|drafting|drafted|reviewed|final
    target_words INTEGER DEFAULT 0,
    word_count  INTEGER DEFAULT 0,
    guidance    TEXT DEFAULT '',                 -- instructions handed to the writer agent
    cited_paper_ids TEXT DEFAULT '[]',           -- JSON
    meta        TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(document_id, key)
);
CREATE INDEX IF NOT EXISTS idx_sections_doc ON sections(document_id, ordering);

-- Snapshots for version compare. Content-addressed blobs keep repeated
-- snapshots of a barely-changed manuscript cheap.
CREATE TABLE IF NOT EXISTS snapshots (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    label       TEXT DEFAULT '',
    kind        TEXT DEFAULT 'manual',           -- manual|auto|pre_agent|post_agent
    git_commit  TEXT DEFAULT '',
    payload     TEXT DEFAULT '{}',               -- JSON {doc_id: {section_key: content}}
    stats       TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_project ON snapshots(project_id, created_at);

-- One agent pipeline execution.
CREATE TABLE IF NOT EXISTS agent_runs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES projects(id) ON DELETE CASCADE,
    pipeline    TEXT NOT NULL,                   -- full_auto|section|stitch|custom
    mode        TEXT DEFAULT '',
    status      TEXT DEFAULT 'pending',          -- pending|running|paused|done|failed|cancelled
    request     TEXT DEFAULT '{}',
    result      TEXT DEFAULT '{}',
    error       TEXT DEFAULT '',
    tokens_in   INTEGER DEFAULT 0,
    tokens_out  INTEGER DEFAULT 0,
    cost_usd    REAL DEFAULT 0,
    started_at  TEXT,
    finished_at TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_project ON agent_runs(project_id, created_at);

-- Per-agent step inside a run: the full prompt/response pair is kept so the
-- user can audit exactly what the LLM was told.
CREATE TABLE IF NOT EXISTS agent_steps (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    ordering    INTEGER DEFAULT 0,
    agent       TEXT NOT NULL,
    title       TEXT DEFAULT '',
    status      TEXT DEFAULT 'pending',
    model       TEXT DEFAULT '',
    prompt      TEXT DEFAULT '',
    output      TEXT DEFAULT '',
    tokens_in   INTEGER DEFAULT 0,
    tokens_out  INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    error       TEXT DEFAULT '',
    meta        TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steps_run ON agent_steps(run_id, ordering);

-- Skill registry mirror. Source of truth is the SKILL.md on disk; this table
-- caches metadata + enablement so listing does not stat the filesystem.
CREATE TABLE IF NOT EXISTS skills (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    version     TEXT DEFAULT '0.1.0',
    scope       TEXT DEFAULT 'user',             -- builtin|user|project
    project_id  TEXT REFERENCES projects(id) ON DELETE CASCADE,
    description TEXT DEFAULT '',
    triggers    TEXT DEFAULT '[]',               -- JSON [str] phrases that suggest this skill
    applies_to  TEXT DEFAULT '[]',               -- JSON [agent role]
    tags        TEXT DEFAULT '[]',
    path        TEXT DEFAULT '',                 -- directory containing SKILL.md
    enabled     INTEGER DEFAULT 1,
    author      TEXT DEFAULT '',
    origin      TEXT DEFAULT 'manual',           -- manual|llm|builtin|imported
    checksum    TEXT DEFAULT '',                 -- detects on-disk edits
    usage_count INTEGER DEFAULT 0,
    last_used_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Long-running work (search, analysis, agent run, export) tracked so the UI
-- can show progress and cancel. Rows survive restart as 'failed' orphans,
-- which init_db() reconciles.
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    project_id  TEXT,
    status      TEXT DEFAULT 'queued',           -- queued|running|done|failed|cancelled
    progress    REAL DEFAULT 0,
    message     TEXT DEFAULT '',
    payload     TEXT DEFAULT '{}',
    result      TEXT DEFAULT '{}',
    error       TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);

-- Aggregated LLM spend, one row per call. Powers the usage panel.
CREATE TABLE IF NOT EXISTS llm_usage (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    provider    TEXT DEFAULT '',
    model       TEXT DEFAULT '',
    purpose     TEXT DEFAULT '',
    run_id      TEXT,
    tokens_in   INTEGER DEFAULT 0,
    tokens_out  INTEGER DEFAULT 0,
    cost_usd    REAL DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    ok          INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_usage_created ON llm_usage(created_at);

-- Small key/value store for app state that is not user settings
-- (last opened project, migration bookkeeping, dismissed hints).
CREATE TABLE IF NOT EXISTS app_state (
    key        TEXT PRIMARY KEY,
    value      TEXT DEFAULT '',
    updated_at TEXT NOT NULL
);
"""

_SCHEMA_V2 = """
-- Files, folders and generated notes copied into the selected workbench.
-- managed_path is relative to <workbench>/.papercreator so the whole folder
-- remains portable across drives and machines. original_path is provenance,
-- never the runtime source of truth.
CREATE TABLE IF NOT EXISTS workbench_resources (
    id            TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,                -- idea|reference_paper|own_paper|code_project|dataset|supplementary|inbox
    title         TEXT NOT NULL,
    description   TEXT DEFAULT '',
    managed_path  TEXT NOT NULL,
    original_path TEXT DEFAULT '',
    is_directory  INTEGER DEFAULT 0,
    mime_type     TEXT DEFAULT '',
    size_bytes    INTEGER DEFAULT 0,
    checksum      TEXT DEFAULT '',
    project_id    TEXT REFERENCES projects(id) ON DELETE SET NULL,
    paper_id      TEXT REFERENCES papers(id) ON DELETE SET NULL,
    metadata      TEXT DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE(managed_path)
);
CREATE INDEX IF NOT EXISTS idx_workbench_resources_kind
    ON workbench_resources(kind, updated_at);
CREATE INDEX IF NOT EXISTS idx_workbench_resources_project
    ON workbench_resources(project_id, updated_at);
"""

_SCHEMA_V3 = """
-- Bilingual manuscripts need independent length targets.  English words and
-- Chinese character/word units are not interchangeable, so one shared target
-- made the paired outline misleading and impossible to customise.
ALTER TABLE sections ADD COLUMN target_words_zh INTEGER DEFAULT 0;
"""

_SCHEMA_V4 = """
-- Reusable prompt templates are workbench data, not browser preferences.  A
-- NULL project_id makes a template visible across the selected workbench;
-- project templates disappear automatically when their project is deleted.
CREATE TABLE IF NOT EXISTS prompt_templates (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    content     TEXT NOT NULL,
    variables   TEXT DEFAULT '[]',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prompt_templates_project
    ON prompt_templates(project_id, updated_at);
"""

_SCHEMA_V5 = """
-- Assistant conversations are independent from prompt templates and agent runs.
-- A NULL project_id is a workbench-scoped general conversation; project
-- conversations are removed with their project. Messages are append-only.
CREATE TABLE IF NOT EXISTS assistant_threads (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES projects(id) ON DELETE CASCADE,
    title       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assistant_threads_project
    ON assistant_threads(project_id, updated_at);
CREATE TABLE IF NOT EXISTS assistant_messages (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL REFERENCES assistant_threads(id) ON DELETE CASCADE,
    ordering    INTEGER NOT NULL,
    role        TEXT NOT NULL CHECK(role IN ('user','assistant')),
    content     TEXT NOT NULL,
    actions     TEXT DEFAULT '[]',
    meta        TEXT DEFAULT '{}',
    created_at  TEXT NOT NULL
    ,UNIQUE(thread_id, ordering)
);
CREATE INDEX IF NOT EXISTS idx_assistant_messages_thread
    ON assistant_messages(thread_id, ordering);
"""

_SCHEMA_V6 = """
-- Conversation archives are restored as copies with fresh local IDs.  This
-- mapping makes an exact archive import idempotent without treating a later,
-- changed source thread as the same local record.  scope_key is explicit text
-- because SQLite UNIQUE permits multiple NULL values.
CREATE TABLE IF NOT EXISTS assistant_thread_imports (
    id                 TEXT PRIMARY KEY,
    scope_key          TEXT NOT NULL,
    source_thread_id   TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    thread_id          TEXT NOT NULL REFERENCES assistant_threads(id) ON DELETE CASCADE,
    source_scope       TEXT NOT NULL DEFAULT '{}',
    imported_at        TEXT NOT NULL,
    UNIQUE(scope_key, source_thread_id, source_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_assistant_thread_imports_thread
    ON assistant_thread_imports(thread_id);
"""

# Append-only. (version, sql). Version N runs when user_version < N.
MIGRATIONS: list[tuple[int, str]] = [
    (1, _SCHEMA_V1),
    (2, _SCHEMA_V2),
    (3, _SCHEMA_V3),
    (4, _SCHEMA_V4),
    (5, _SCHEMA_V5),
    (6, _SCHEMA_V6),
]


def init_db() -> None:
    """Create/upgrade the schema. Safe to call repeatedly and concurrently."""
    global _initialised_for
    target = db_path()
    with _init_lock:
        if _initialised_for == target:
            return
        conn = connect()
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
        for version, sql in MIGRATIONS:
            if current >= version:
                continue
            log.info("applying database migration %s", version)
            # BEGIN/COMMIT must live *inside* the script: executescript() issues
            # an implicit COMMIT of any pending transaction before it runs, so
            # wrapping the call in transaction() would leave nothing to commit.
            # PRAGMA cannot be parameterised, hence the f-string on an int.
            script = f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version={int(version)};\nCOMMIT;"
            try:
                conn.executescript(script)
            except BaseException:
                # A failed script can leave the transaction open.
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise
            current = version
        _reconcile_orphan_jobs(conn)
        _initialised_for = target


def _reconcile_orphan_jobs(conn: sqlite3.Connection) -> None:
    """Jobs marked running when the process died can never finish.

    Left alone they would show a permanent spinner in the UI, so mark them
    failed at startup with an explicit reason.
    """
    cur = conn.execute(
        "UPDATE jobs SET status='failed', "
        "error='interrupted by application restart', "
        "finished_at=datetime('now') "
        "WHERE status IN ('queued','running')"
    )
    if cur.rowcount:
        log.warning("marked %s interrupted job(s) as failed", cur.rowcount)
    conn.execute(
        "UPDATE agent_runs SET status='failed', "
        "error=COALESCE(NULLIF(error,''),'interrupted by application restart'), "
        "finished_at=datetime('now') WHERE status IN ('pending','running')"
    )


def vacuum() -> None:
    """Reclaim space after large deletions. Blocking; exposed via maintenance."""
    connect().execute("VACUUM")


def stats() -> dict[str, Any]:
    """Row counts + file size for the status view."""
    tables = [
        "papers", "projects", "collections", "collection_items", "searches",
        "embeddings", "analyses", "documents", "sections", "snapshots",
        "agent_runs", "agent_steps", "skills", "jobs", "llm_usage",
        "workbench_resources", "prompt_templates", "assistant_threads",
        "assistant_messages", "assistant_thread_imports",
    ]
    conn = connect()
    counts: dict[str, Any] = {}
    for table in tables:
        try:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error:
            counts[table] = None
    path = db_path()
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "schema_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
        "counts": counts,
    }

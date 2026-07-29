"""Domain models shared across subsystems.

These pydantic models are the contract between retrieval, analysis, writing and
the HTTP API. Provider-specific shapes are converted into :class:`Paper` at the
provider boundary so nothing downstream needs to know where a record came from.

Field naming stays snake_case here; the API layer serialises with pydantic's
default (snake_case) and the TypeScript client mirrors it exactly - no implicit
camelCase conversion, to keep grep-ability between the two codebases.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .util import (
    coerce_int,
    collapse_ws,
    normalize_arxiv_id,
    normalize_doi,
    stable_hash,
)

# --------------------------------------------------------------------- papers


class Author(BaseModel):
    name: str = ""
    affiliation: str = ""
    orcid: str = ""

    @field_validator("name", mode="before")
    @classmethod
    def _clean(cls, v: Any) -> str:
        return collapse_ws(str(v or ""))


PaperOrigin = Literal["retrieved", "manual", "idea", "own_paper"]
ReadStatus = Literal["unread", "skimmed", "read"]


class Paper(BaseModel):
    """One scholarly work, provider-agnostic.

    ``id`` is derived, not random: :meth:`compute_id` prefers DOI, then arXiv
    id, then PubMed/OpenAlex ids, and only falls back to a title+year hash.
    That makes ingesting the same paper twice from different providers converge
    on one row without a lookup table.
    """

    id: str = ""
    title: str = ""
    abstract: str = ""
    authors: list[Author] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    venue_type: str = ""
    doi: str = ""
    arxiv_id: str = ""
    pmid: str = ""
    openalex_id: str = ""
    s2_id: str = ""
    url: str = ""
    pdf_url: str = ""
    pdf_path: str = ""
    is_open_access: bool = False
    citation_count: int = 0
    reference_count: int = 0
    fields_of_study: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    references_ids: list[str] = Field(default_factory=list)
    language: str = ""
    source_providers: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    origin: PaperOrigin = "retrieved"
    notes: str = ""
    rating: int = 0
    read_status: ReadStatus = "unread"
    tags: list[str] = Field(default_factory=list)
    # Populated by the retrieval pipeline / reranker, not persisted on papers.
    score: float = 0.0
    created_at: str = ""
    updated_at: str = ""

    @field_validator("title", "abstract", "venue", mode="before")
    @classmethod
    def _clean_text(cls, v: Any) -> str:
        return collapse_ws(str(v or ""))

    @field_validator("doi", mode="before")
    @classmethod
    def _clean_doi(cls, v: Any) -> str:
        return normalize_doi(v if isinstance(v, str) else (str(v) if v else ""))

    @field_validator("arxiv_id", mode="before")
    @classmethod
    def _clean_arxiv(cls, v: Any) -> str:
        return normalize_arxiv_id(v if isinstance(v, str) else (str(v) if v else ""))

    @field_validator("year", mode="before")
    @classmethod
    def _clean_year(cls, v: Any) -> int | None:
        if v in (None, "", 0):
            return None
        year = coerce_int(v, 0)
        # Guard against provider junk (year 202, 20230) reaching the timeline.
        return year if 1600 <= year <= 2100 else None

    @field_validator("keywords", "fields_of_study", mode="before")
    @classmethod
    def _split_keyword_lists(cls, v: Any) -> list[str]:
        """Normalise keyword lists that arrive as packed strings.

        Providers are inconsistent: some return a proper list, others a single
        string joining terms with ``|``, ``;`` or ``,``. Observed live in DOAJ
        data: one "keyword" was
        ``"molecular property prediction|causal|graph neural network(gnn)"``,
        which then became a cluster label. Splitting here means every consumer
        (keywords, heatmap layers, BibTeX) sees individual terms.
        """
        if v in (None, "", []):
            return []
        items = v if isinstance(v, (list, tuple)) else [v]
        out: list[str] = []
        for item in items:
            text = str(item or "")
            if not text.strip():
                continue
            # Only split on comma when the string looks like a packed list
            # rather than a phrase containing a comma.
            separators = "|;" if "," not in text or len(text) < 40 else "|;,"
            parts = [text]
            for separator in separators:
                parts = [p for chunk in parts for p in chunk.split(separator)]
            for part in parts:
                cleaned = collapse_ws(part).strip(" .,;|")
                if cleaned and len(cleaned) < 120:
                    out.append(cleaned)
        seen: set[str] = set()
        deduped: list[str] = []
        for term in out:
            key = term.lower()
            if key not in seen:
                seen.add(key)
                deduped.append(term)
        return deduped

    def compute_id(self) -> str:
        """Stable identity for cross-provider dedupe. Order matters."""
        if self.doi:
            return f"doi_{stable_hash(self.doi, length=20)}"
        if self.arxiv_id:
            return f"arx_{stable_hash(self.arxiv_id, length=20)}"
        if self.pmid:
            return f"pmid_{stable_hash(self.pmid, length=20)}"
        if self.openalex_id:
            return f"oa_{stable_hash(self.openalex_id, length=20)}"
        if self.s2_id:
            return f"s2_{stable_hash(self.s2_id, length=20)}"
        return f"t_{stable_hash(self.title.lower(), self.year or 0, length=20)}"

    def ensure_id(self) -> "Paper":
        if not self.id:
            self.id = self.compute_id()
        return self

    def author_names(self, limit: int = 0) -> list[str]:
        names = [a.name for a in self.authors if a.name]
        return names[:limit] if limit else names

    def citation_text(self) -> str:
        """One-line human reference, used in prompts and hover cards."""
        names = self.author_names()
        if not names:
            who = "Anonymous"
        elif len(names) == 1:
            who = names[0]
        elif len(names) <= 3:
            who = ", ".join(names)
        else:
            who = f"{names[0]} et al."
        year = self.year or "n.d."
        venue = f". {self.venue}" if self.venue else ""
        return f"{who} ({year}). {self.title}{venue}"

    def embedding_text(self) -> str:
        """Text fed to the embedder.

        Title is repeated ahead of the abstract because it carries the strongest
        topical signal and short-context models truncate the tail.
        """
        parts = [self.title, self.title]
        if self.abstract:
            parts.append(self.abstract)
        if self.keywords:
            parts.append(" ".join(self.keywords))
        if self.fields_of_study:
            parts.append(" ".join(self.fields_of_study))
        return "\n".join(p for p in parts if p)


# ------------------------------------------------------------------ retrieval

SearchMode = Literal["keyword", "idea", "paper", "advanced"]


class SearchRequest(BaseModel):
    """Normalised search input, shared by every provider.

    Providers translate this into their own query language and ignore filters
    they cannot express - :class:`ProviderCapabilities` declares which, so the
    pipeline can post-filter instead.
    """

    query: str = ""
    mode: SearchMode = "keyword"
    # Free-form seed for idea/paper modes: an abstract, a paragraph, a title.
    seed_text: str = ""
    providers: list[str] = Field(default_factory=list)
    limit_per_provider: int = 50
    total_limit: int = 300
    year_from: int | None = None
    year_to: int | None = None
    open_access_only: bool = False
    venues: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    fields_of_study: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    sort: Literal["relevance", "date", "citations"] = "relevance"
    # Expanded query variants (LLM- or rule-generated). Empty = use `query`.
    expanded_queries: list[str] = Field(default_factory=list)
    project_id: str = ""
    collection_name: str = ""
    use_cache: bool = True
    # Part of the reproducible request, not a UI-only hint. Background jobs and
    # history reruns must make the same expansion choice as the original search.
    use_llm_expansion: bool = True

    def effective_queries(self) -> list[str]:
        out = [q for q in [self.query, *self.expanded_queries] if q and q.strip()]
        seen: set[str] = set()
        result = []
        for q in out:
            key = q.strip().lower()
            if key not in seen:
                seen.add(key)
                result.append(q.strip())
        return result


class ProviderStats(BaseModel):
    """Per-provider outcome of one search. Always returned, even on failure,
    so the UI can show which sources contributed."""

    provider: str
    count: int = 0
    duration_ms: int = 0
    outcome: Literal[
        "success",
        "unavailable",
        "rate_limited",
        "timeout",
        "authentication_error",
        "http_error",
        "network_error",
        "invalid_response",
        "provider_error",
        "unexpected_error",
    ] = "success"
    error: str = ""
    error_code: str = ""
    retryable: bool = False
    http_status: int | None = None
    retry_after_s: float | None = None
    hint: str = ""
    from_cache: bool = False
    queries_run: int = 0
    truncated: bool = False


class SearchResponse(BaseModel):
    search_id: str = ""
    query: str = ""
    mode: SearchMode = "keyword"
    papers: list[Paper] = Field(default_factory=list)
    stats: list[ProviderStats] = Field(default_factory=list)
    total_before_dedupe: int = 0
    total_after_dedupe: int = 0
    duplicates_merged: int = 0
    warnings: list[str] = Field(default_factory=list)
    request: dict[str, Any] = Field(default_factory=dict)


# ------------------------------------------------------------------- analysis


class AnalysisConfig(BaseModel):
    """Everything that determines a landscape's geometry.

    Persisted with the analysis so a run is reproducible and so
    :mod:`analysis.incremental` can verify a new point is being projected into
    a space built with the same settings.
    """

    embedding_backend: str = "auto"
    embedding_model: str = ""
    reducer: str = "auto"
    clusterer: str = "auto"
    dimensions: Literal[2, 3] = 3
    n_neighbors: int = 15
    min_dist: float = 0.1
    metric: str = "cosine"
    min_cluster_size: int = 5
    n_clusters: int = 0  # 0 = auto (kmeans/agglomerative only)
    keyword_top_k: int = 12
    keyword_method: Literal["ctfidf", "tfidf", "yake_like"] = "ctfidf"
    heatmap_grid: int = 40
    heatmap_bandwidth: float = 0.0  # 0 = Scott's rule
    detect_gaps: bool = True
    gap_min_score: float = 0.35
    random_state: int = 42
    label_clusters_with_llm: bool = False


class ClusterInfo(BaseModel):
    id: int
    label: str = ""
    label_zh: str = ""
    size: int = 0
    keywords: list[str] = Field(default_factory=list)
    centroid: list[float] = Field(default_factory=list)
    representative_paper_ids: list[str] = Field(default_factory=list)
    year_min: int | None = None
    year_max: int | None = None
    year_median: float | None = None
    mean_citations: float = 0.0
    coherence: float = 0.0  # mean cosine to centroid in embedding space
    summary: str = ""


class PaperPoint(BaseModel):
    paper_id: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    cluster: int = -1
    outlier: float = 0.0
    is_seed: bool = False
    density: float = 0.0


GapKind = Literal[
    "sparse_region", "cluster_bridge", "temporal_stale", "underexplored_pair",
    "low_density_frontier",
]


class GapCandidate(BaseModel):
    """A hypothesised research gap.

    Every gap carries ``evidence`` (the concrete measurements behind it) so the
    UI never asserts a gap without letting the user check the reasoning. These
    are heuristics over metadata, not proof that nobody has done the work.
    """

    id: str = ""
    kind: GapKind = "sparse_region"
    score: float = 0.0
    center: list[float] = Field(default_factory=list)
    radius: float = 0.0
    related_cluster_ids: list[int] = Field(default_factory=list)
    nearest_paper_ids: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    description: str = ""
    description_zh: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class KeywordStat(BaseModel):
    term: str
    count: int = 0
    score: float = 0.0
    first_year: int | None = None
    last_year: int | None = None
    trend: float = 0.0  # >0 rising, <0 declining (see analysis/keywords.py)
    cluster_ids: list[int] = Field(default_factory=list)


class HeatmapData(BaseModel):
    """Grid densities over the projection, plus keyword-specific layers.

    ``grid`` is row-major ``[y][x]``; ``layers`` maps a keyword to the same
    shape so the frontend can blend or switch layers without a round trip.
    """

    grid_size: int = 0
    bounds: list[float] = Field(default_factory=list)  # [xmin,xmax,ymin,ymax]
    grid: list[list[float]] = Field(default_factory=list)
    max_density: float = 0.0
    layers: dict[str, list[list[float]]] = Field(default_factory=dict)
    z_slices: list[dict[str, Any]] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    id: str = ""
    project_id: str = ""
    name: str = ""
    config: AnalysisConfig = Field(default_factory=AnalysisConfig)
    embedding_model: str = ""
    reducer: str = ""
    clusterer: str = ""
    points: list[PaperPoint] = Field(default_factory=list)
    clusters: list[ClusterInfo] = Field(default_factory=list)
    keywords: list[KeywordStat] = Field(default_factory=list)
    gaps: list[GapCandidate] = Field(default_factory=list)
    heatmap: HeatmapData = Field(default_factory=HeatmapData)
    metrics: dict[str, Any] = Field(default_factory=dict)
    n_papers: int = 0
    n_clusters: int = 0
    warnings: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class PositionResult(BaseModel):
    """Where a newly added idea/paper landed in an existing landscape.

    Produced by :mod:`analysis.incremental` for requirement "add my own idea and
    see where it sits". Reuses the stored projector so the coordinates are
    comparable with the existing points.
    """

    paper_id: str
    analysis_id: str
    point: PaperPoint
    method: Literal["exact_transform", "interpolated"]
    nearest_cluster: int = -1
    nearest_cluster_label: str = ""
    cluster_distance: float = 0.0
    nearest_papers: list[dict[str, Any]] = Field(default_factory=list)
    local_density: float = 0.0
    density_percentile: float = 0.0
    novelty: float = 0.0  # 1 - normalised local density, 0..1
    nearest_gaps: list[dict[str, Any]] = Field(default_factory=list)
    interpretation: str = ""
    interpretation_zh: str = ""


# -------------------------------------------------------------------- writing

SectionStatus = Literal["empty", "drafting", "drafted", "reviewed", "final"]


class SectionModel(BaseModel):
    id: str = ""
    document_id: str = ""
    parent_id: str | None = None
    key: str = ""
    title: str = ""
    title_zh: str = ""
    ordering: int = 0
    level: int = 1
    content: str = ""
    content_zh: str = ""
    status: SectionStatus = "empty"
    target_words: int = 0
    target_words_zh: int = 0
    word_count: int = 0
    guidance: str = ""
    cited_paper_ids: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    children: list["SectionModel"] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class DocumentModel(BaseModel):
    id: str = ""
    project_id: str = ""
    kind: str = "manuscript"
    title: str = ""
    format: Literal["markdown", "latex"] = "markdown"
    rel_path: str = ""
    sections: list[SectionModel] = Field(default_factory=list)
    word_count: int = 0
    created_at: str = ""
    updated_at: str = ""


class ProjectModel(BaseModel):
    id: str = ""
    slug: str = ""
    title: str = ""
    title_zh: str = ""
    description: str = ""
    idea: str = ""
    research_field: str = ""
    target_venue: str = ""
    template_id: str = "generic"
    language: str = "en"
    bilingual: bool = True
    citation_style: str = "ieee"
    path: str = ""
    git_enabled: bool = True
    status: str = "active"
    settings: dict[str, Any] = Field(default_factory=dict)
    # Denormalised counters for the project list; recomputed on read.
    paper_count: int = 0
    word_count: int = 0
    section_count: int = 0
    created_at: str = ""
    updated_at: str = ""


SectionModel.model_rebuild()


# --------------------------------------------------------------- workbench

WorkbenchResourceKind = Literal[
    "idea",
    "reference_paper",
    "own_paper",
    "code_project",
    "dataset",
    "supplementary",
    "inbox",
]


class WorkbenchResource(BaseModel):
    """One managed input copied into the selected ``.papercreator`` folder.

    ``managed_path`` is stored relative to the managed home, not as an absolute
    path. A complete workbench can therefore be moved to another drive without
    rewriting every row. ``original_path`` is provenance only and may stop
    existing after the import; runtime access always uses ``managed_path``.
    """

    id: str = ""
    kind: WorkbenchResourceKind = "inbox"
    title: str = ""
    description: str = ""
    managed_path: str = ""
    original_path: str = ""
    is_directory: bool = False
    mime_type: str = ""
    size_bytes: int = 0
    checksum: str = ""
    project_id: str = ""
    paper_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

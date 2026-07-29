/**
 * Types mirroring the backend's pydantic models.
 *
 * Field names are snake_case here deliberately: they match
 * `papercreator.core.models` exactly, so a field can be grepped across both
 * codebases. The backend does no camelCase conversion, and inventing one on this
 * side would break that.
 *
 * Exception: SSE event payloads are camelCase, because they are built by hand in
 * `core/events.py` for the browser. Those live in `events.ts`.
 */

export interface Author {
  name: string;
  affiliation: string;
  orcid: string;
}

export type PaperOrigin = "retrieved" | "manual" | "idea" | "own_paper";
export type ReadStatus = "unread" | "skimmed" | "read";

export type WorkbenchResourceKind =
  | "idea"
  | "reference_paper"
  | "own_paper"
  | "code_project"
  | "dataset"
  | "supplementary"
  | "inbox";

export interface WorkbenchResource {
  id: string;
  kind: WorkbenchResourceKind;
  title: string;
  description: string;
  managed_path: string;
  original_path: string;
  path: string;
  exists: boolean;
  is_directory: boolean;
  mime_type: string;
  size_bytes: number;
  checksum: string;
  project_id: string;
  paper_id: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface WorkbenchImportResult {
  resource: WorkbenchResource;
  papers: Paper[];
  warnings?: string[];
}

export interface WorkbenchImportAccepted {
  job_id: string;
  status: "queued";
  kind: "resource_import";
  source_path: string;
}

export interface WorkbenchCategory {
  kind: WorkbenchResourceKind;
  label: string;
  label_zh: string;
  description: string;
  description_zh: string;
  path: string;
  count: number;
}

export interface WorkbenchInfo {
  product: string;
  format: string;
  schema_version: number;
  workbench: string;
  managed_directory: string;
  managed_directory_name: string;
  projects_directory: string;
  project_count: number;
  last_project_id: string;
  categories: WorkbenchCategory[];
  storage: { free_bytes: number; total_bytes: number };
  rules: {
    imports_are_copied: boolean;
    external_paths_are_provenance_only: boolean;
    writing_projects_are_separate: boolean;
  };
}

export interface Paper {
  id: string;
  title: string;
  abstract: string;
  authors: Author[];
  year: number | null;
  venue: string;
  venue_type: string;
  doi: string;
  arxiv_id: string;
  pmid: string;
  openalex_id: string;
  s2_id: string;
  url: string;
  pdf_url: string;
  pdf_path: string;
  is_open_access: boolean;
  citation_count: number;
  reference_count: number;
  fields_of_study: string[];
  keywords: string[];
  references_ids: string[];
  language: string;
  source_providers: string[];
  raw: Record<string, unknown>;
  origin: PaperOrigin;
  notes: string;
  rating: number;
  read_status: ReadStatus;
  tags: string[];
  score: number;
  created_at: string;
  updated_at: string;
}

export interface ProviderStats {
  provider: string;
  count: number;
  duration_ms: number;
  outcome:
    | "success"
    | "unavailable"
    | "rate_limited"
    | "timeout"
    | "authentication_error"
    | "http_error"
    | "network_error"
    | "invalid_response"
    | "provider_error"
    | "unexpected_error";
  error: string;
  error_code: string;
  retryable: boolean;
  http_status: number | null;
  retry_after_s: number | null;
  hint: string;
  from_cache: boolean;
  queries_run: number;
  truncated: boolean;
}

export interface SearchResponse {
  search_id: string;
  query: string;
  mode: string;
  papers: Paper[];
  stats: ProviderStats[];
  total_before_dedupe: number;
  total_after_dedupe: number;
  duplicates_merged: number;
  warnings: string[];
  request: Record<string, unknown>;
}

export interface ProviderCapabilities {
  full_text_search: boolean;
  field_search: boolean;
  boolean_operators: boolean;
  year_range: boolean;
  open_access_filter: boolean;
  venue_filter: boolean;
  author_filter: boolean;
  sort_by_date: boolean;
  sort_by_citations: boolean;
  returns_abstract: boolean;
  returns_citations: boolean;
  returns_references: boolean;
  returns_pdf_url: boolean;
  max_results_per_request: number;
  supports_pagination: boolean;
  semantic_query: boolean;
}

export interface ProviderInfo {
  id: string;
  name: string;
  name_zh: string;
  description: string;
  description_zh: string;
  homepage: string;
  docs_url: string;
  tier: "free" | "freemium" | "key";
  coverage: string;
  disciplines: string[];
  requires_key: boolean;
  key_setting: string;
  signup_url: string;
  available: boolean;
  unavailable_reason: string;
  has_key: boolean;
  enabled: boolean;
  capabilities: ProviderCapabilities;
  rate_limit: {
    min_interval_s: number;
    max_concurrency: number;
    max_retries: number;
    max_queries: number;
    note: string;
  };
}

// ---------------------------------------------------------------- projects

export interface Project {
  id: string;
  slug: string;
  title: string;
  title_zh: string;
  description: string;
  idea: string;
  research_field: string;
  target_venue: string;
  template_id: string;
  language: string;
  bilingual: boolean;
  citation_style: string;
  path: string;
  git_enabled: boolean;
  status: string;
  settings: Record<string, unknown>;
  paper_count: number;
  word_count: number;
  section_count: number;
  created_at: string;
  updated_at: string;
}

export type SectionStatus = "empty" | "drafting" | "drafted" | "reviewed" | "final";

export interface Section {
  id: string;
  document_id: string;
  parent_id: string | null;
  key: string;
  title: string;
  title_zh: string;
  ordering: number;
  level: number;
  content: string;
  content_zh: string;
  status: SectionStatus;
  target_words: number;
  target_words_zh: number;
  word_count: number;
  guidance: string;
  cited_paper_ids: string[];
  meta: Record<string, unknown>;
  children: Section[];
  created_at: string;
  updated_at: string;
}

export interface DocumentModel {
  id: string;
  project_id: string;
  kind: string;
  title: string;
  format: "markdown" | "latex";
  rel_path: string;
  sections: Section[];
  word_count: number;
  created_at: string;
  updated_at: string;
}

export interface ManuscriptSyncStatus {
  document_id: string;
  project_id: string;
  path: string;
  state_file: string;
  state:
    | "in_sync"
    | "database_changed"
    | "disk_changed"
    | "diverged"
    | "untracked_equal"
    | "database_only"
    | "disk_only"
    | "untracked_divergence";
  baseline_present: boolean;
  baseline_error: string;
  db_changed: boolean;
  disk_changed: boolean;
  can_flush: boolean;
  can_reindex: boolean;
  synced_at: string;
  section_baseline_present: boolean;
  section_changes: {
    database: string[];
    disk: string[];
    conflicts: string[];
    merge_blockers: string[];
  };
  can_auto_merge: boolean;
  merge_preview_token: string;
  database: {
    digest: string;
    sections: number;
    files: string[];
  };
  disk: {
    digest: string;
    files_count: number;
    files: string[];
    directory_exists: boolean;
  };
}

export interface ManuscriptStats {
  document_id: string;
  project_id: string;
  sections: number;
  words: number;
  words_zh: number;
  target_words: number;
  target_words_zh: number;
  completion: number;
  completion_zh: number;
  by_status: Record<string, number>;
  empty_sections: string[];
  papers_in_project: number;
  papers_cited: number;
  citation_coverage: number;
  sections_detail: {
    key: string;
    title: string;
    status: SectionStatus;
    words: number;
    target: number;
    words_zh: number;
    target_zh: number;
    citations: number;
    completion: number;
    completion_zh: number;
  }[];
}

export interface TranslationSectionPreview {
  key: string;
  title: string;
  title_zh: string;
  source_sha256: string;
  target_sha256: string;
  source_characters: number;
  text: string;
  translated_characters: number;
}

export interface TranslationJobResult {
  mode: "text" | "project";
  text?: string;
  project_id?: string;
  provider: "mymemory" | "llm";
  source: string;
  target: string;
  sections?: TranslationSectionPreview[];
  section_count?: number;
  source_characters: number;
  translated_characters?: number;
  requests?: number;
  retries?: number;
  preview_only: true;
  note: string;
}

// ---------------------------------------------------------------- analysis

export interface PaperPoint {
  paper_id: string;
  x: number;
  y: number;
  z: number;
  cluster: number;
  outlier: number;
  is_seed: boolean;
  density: number;
}

export interface ClusterInfo {
  id: number;
  label: string;
  label_zh: string;
  size: number;
  keywords: string[];
  centroid: number[];
  representative_paper_ids: string[];
  year_min: number | null;
  year_max: number | null;
  year_median: number | null;
  mean_citations: number;
  coherence: number;
  summary: string;
}

export type GapKind =
  | "sparse_region"
  | "cluster_bridge"
  | "temporal_stale"
  | "underexplored_pair"
  | "low_density_frontier";

export interface GapCandidate {
  id: string;
  kind: GapKind;
  score: number;
  center: number[];
  radius: number;
  related_cluster_ids: number[];
  nearest_paper_ids: string[];
  keywords: string[];
  description: string;
  description_zh: string;
  evidence: Record<string, unknown>;
}

export interface KeywordStat {
  term: string;
  count: number;
  score: number;
  first_year: number | null;
  last_year: number | null;
  trend: number;
  cluster_ids: number[];
}

export interface HeatmapData {
  grid_size: number;
  bounds: number[];
  grid: number[][];
  max_density: number;
  layers: Record<string, number[][]>;
  layer_names?: string[];
  z_slices: { z_min: number; z_max: number; count: number; grid: number[][] }[];
}

export interface AnalysisSummary {
  analysis_id: string;
  project_id: string;
  name: string;
  n_papers: number;
  n_clusters: number;
  embedding_model: string;
  reducer: string;
  clusterer: string;
  metrics: Record<string, unknown>;
  clusters: ClusterInfo[];
  gaps: GapCandidate[];
  keyword_count: number;
  warnings: string[];
  created_at: string;
}

export interface AnalysisDetail extends AnalysisSummary {
  config: Record<string, unknown>;
  keywords: KeywordStat[];
  points: PaperPoint[];
  heatmap?: HeatmapData;
  papers?: Paper[];
  missing_papers?: string[];
}

export interface AnalysisPaperRow {
  paper_id: string;
  x: number;
  y: number;
  z: number;
  cluster: number;
  cluster_label: string;
  outlier: number;
  density: number;
  is_seed: boolean;
  title: string;
  year: number | null;
  venue: string;
  citations: number;
  authors: string[];
  origin: string;
  missing: boolean;
}

export interface PositionResult {
  paper_id: string;
  analysis_id: string;
  point: PaperPoint;
  method: "exact_transform" | "interpolated";
  nearest_cluster: number;
  nearest_cluster_label: string;
  cluster_distance: number;
  nearest_papers: {
    paper_id: string;
    similarity: number;
    title: string;
    year: number | null;
    cluster: number;
  }[];
  local_density: number;
  density_percentile: number;
  novelty: number;
  nearest_gaps: {
    id: string;
    kind: GapKind;
    score: number;
    distance: number;
    inside: boolean;
    description: string;
    keywords: string[];
  }[];
  interpretation: string;
  interpretation_zh: string;
}

// ------------------------------------------------------------------ agents

export interface AgentPipeline {
  id: string;
  name: string;
  name_zh: string;
  description: string;
  description_zh: string;
  steps: string[];
  typical_calls: string;
}

export interface AgentRole {
  name: string;
  title: string;
  title_zh: string;
  description: string;
  requires: string[];
  prefers_fast_model: boolean;
  per_section: boolean;
}

export interface LlmFailureDiagnostic {
  outcome: string;
  error_code: string;
  retryable: boolean;
  http_status: number | null;
  retry_after_s: number | null;
  hint: string;
  provider: string;
  model: string;
  message: string;
  error_type: string;
  context?: string;
  partial_output_chars?: number;
  partial_tokens_in?: number;
  partial_tokens_out?: number;
}

export interface AgentStep {
  id: string;
  run_id: string;
  ordering: number;
  agent: string;
  title: string;
  status: string;
  model: string;
  output: string;
  prompt?: string;
  tokens_in: number;
  tokens_out: number;
  duration_ms: number;
  error: string;
  meta: Record<string, unknown>;
  created_at: string;
}

export type AgentQualityCheckStatus = "pass" | "warn" | "fail" | "not_run" | "not_applicable";

export interface AgentQualityCheck {
  id: string;
  status: AgentQualityCheckStatus;
  message: string;
  method: "deterministic" | "model_assisted" | string;
  evidence: Record<string, unknown>;
}

export interface AgentQualityReport {
  schema_version: number;
  generated_at: string;
  run_status: string;
  gate: "pass" | "warn" | "fail" | "unavailable";
  summary: { pass: number; warn: number; fail: number; not_run: number };
  metrics: {
    sections_evaluated: number;
    modified_sections: number;
    words: number;
    citation_marker_occurrences: number;
    distinct_citation_keys: number;
    invalid_citation_keys: number;
    cited_papers: number;
    sections_with_citations: number;
    citation_density_per_1000_words: number;
    questionable_citations: number;
    uncited_claims: number;
    high_critic_issues: number;
    medium_critic_issues: number;
  };
  checks: AgentQualityCheck[];
  sections: Array<{
    section_key: string;
    words: number;
    target_words: number;
    modified_by_run: boolean;
    citation_occurrences: number;
    citation_keys: string[];
    invalid_keys: string[];
    cited_paper_ids: string[];
    [key: string]: unknown;
  }>;
  citation_registry: {
    paper_count: number;
    keys_used: string[];
    cited_paper_ids: string[];
    papers: Array<{
      paper_id: string;
      key: string;
      title: string;
      year: number | null;
      doi: string;
      url: string;
      pdf_path: string;
      abstract?: string;
      abstract_sha256?: string;
      abstract_available: boolean;
    }>;
  };
  review_requirements?: {
    rubric_version: number;
    accepted_run_statuses: string[];
    accepted_automatic_gates: string[];
    minimum_dimension_score: number;
    reviewer_required: boolean;
    evidence_notes_required: boolean;
    warning_acknowledgement_required: boolean;
    immutable_manuscript_required?: boolean;
    required_section_keys: string[];
    required_paper_ids: string[];
  };
  acceptance: {
    automatic_gate: string;
    human_review_required: boolean;
    human_review_recorded?: boolean;
    semantic_grounding_verified: boolean;
    latest_human_decision: string;
    latest_human_evaluation_id?: string;
    latest_human_rubric_version?: number;
    human_source_evidence_checked?: boolean;
  };
  limitations: string[];
}

export interface AgentReviewManuscriptSection {
  section_key: string;
  title: string;
  primary_text: string;
  primary_text_sha256: string;
  primary_text_chars: number;
  paired_text: string;
  paired_text_sha256: string;
  paired_text_chars: number;
  modified_by_run: boolean;
}

export interface AgentReviewManuscript {
  schema_version: number;
  source_snapshot_id?: string;
  manuscript_fingerprint: string;
  sections: AgentReviewManuscriptSection[];
}

export interface AgentReviewPacket {
  schema_version: number;
  packet_kind: "blind" | "analysis";
  generated_at: string;
  sample_id: string;
  identity_hidden: boolean;
  packet_fingerprint: string;
  evidence_contract: NonNullable<AgentHumanEvaluation["review_target"]>;
  manuscript: AgentReviewManuscript;
  automatic_quality_report: Omit<AgentQualityReport, "acceptance"> & {
    acceptance?: AgentQualityReport["acceptance"];
  };
  source_evidence: Array<Record<string, unknown>>;
  provenance?: Record<string, unknown>;
  human_evaluations?: AgentHumanEvaluation[];
}

export interface AgentHumanEvaluation {
  id: string;
  rubric_version: number;
  reviewer: string;
  decision: "accepted" | "revision_required" | "rejected";
  dimensions: Record<string, number>;
  overall_score: number;
  source_evidence_checked: boolean;
  automatic_warnings_acknowledged?: boolean;
  reviewed_section_keys: string[];
  reviewed_paper_ids?: string[];
  reviewed_manuscript_fingerprint?: string;
  review_mode?: "identified" | "blind";
  review_target?: {
    rubric_version: number;
    run_status: string;
    automatic_gate: string;
    quality_report_schema_version: number;
    quality_report_generated_at: string;
    quality_report_fingerprint: string;
    manuscript_integrity?: "pass" | "fail" | "legacy_unbound";
    manuscript_integrity_problems?: string[];
    manuscript_snapshot_schema_version?: number;
    manuscript_source_snapshot_id?: string;
    manuscript_fingerprint?: string;
    manuscript_section_count?: number;
    required_section_keys: string[];
    required_paper_ids: string[];
  };
  notes: string;
  created_at: string;
}

export interface AgentEvaluationRequest {
  reviewer: string;
  decision: "accepted" | "revision_required" | "rejected";
  factual_grounding: number;
  citation_support: number;
  methodological_soundness: number;
  literature_coverage: number;
  argument_coherence: number;
  writing_clarity: number;
  source_evidence_checked: boolean;
  automatic_warnings_acknowledged: boolean;
  reviewed_section_keys: string[];
  reviewed_paper_ids: string[];
  reviewed_manuscript_fingerprint: string;
  review_mode: "identified" | "blind";
  notes: string;
}

export interface AgentEvaluationSummary {
  schema_version: number;
  runs_scanned: number;
  reviewed_runs: number;
  evaluation_records: number;
  multi_reviewed_runs: number;
  decision_disagreement_runs: number;
  average_score_spread: number;
  latest_decisions: Record<"accepted" | "revision_required" | "rejected", number>;
  dimensions: Record<string, { count: number; average: number; minimum: number; maximum: number }>;
  agreement: {
    status: "available" | "insufficient_data";
    reviewer_count: number;
    review_pair_count: number;
    decision_exact_agreement: number | null;
    decision_kappa: number | null;
    scores: {
      pair_count: number;
      mean_absolute_difference: number | null;
      within_one_rate: number | null;
      quadratic_weighted_kappa: number | null;
    };
    by_dimension: Record<string, {
      pair_count: number;
      mean_absolute_difference: number | null;
      within_one_rate: number | null;
      quadratic_weighted_kappa: number | null;
    }>;
    method: string;
  };
  by_pipeline: Array<{
    label: string;
    reviewed_runs: number;
    latest_decisions: Record<string, number>;
    average_overall_score: number;
  }>;
  by_model: Array<{
    label: string;
    reviewed_runs: number;
    latest_decisions: Record<string, number>;
    average_overall_score: number;
  }>;
}

export interface AgentRun {
  id: string;
  project_id: string;
  pipeline: string;
  mode: string;
  status: "pending" | "running" | "paused" | "done" | "failed" | "cancelled";
  request: Record<string, unknown>;
  result: Record<string, unknown>;
  error: string;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  step_count?: number;
  steps?: AgentStep[];
}

// ------------------------------------------------------------------ skills

export interface SkillInfo {
  id: string;
  name: string;
  description: string;
  version: string;
  scope: "builtin" | "user" | "project";
  author: string;
  origin: string;
  applies_to: string[];
  triggers: string[];
  tags: string[];
  priority: number;
  path: string;
  enabled: boolean;
  usage_count: number;
  last_used_at: string | null;
  editable: boolean;
  instruction_chars: number;
  has_examples: boolean;
  checksum_matches: boolean | null;
}

export interface SkillDraft {
  id: string;
  name: string;
  description: string;
  applies_to: string[];
  triggers: string[];
  tags: string[];
  instructions: string;
  rationale: string;
  origin: string;
  estimated_prompt_tokens: number;
  warnings: string[];
}

// -------------------------------------------------------------- versioning

export interface TimelineEntry {
  kind: "commit" | "snapshot";
  id: string;
  short: string;
  label: string;
  detail: string;
  author: string;
  timestamp: string;
  auto: boolean;
  snapshot_kind?: string;
  git_commit?: string;
}

export interface GitStatus {
  is_repo: boolean;
  path: string;
  git_available: boolean;
  branch?: string;
  ahead?: number;
  behind?: number;
  staged?: { status: string; path: string }[];
  unstaged?: { status: string; path: string }[];
  untracked?: string[];
  conflicted?: string[];
  clean?: boolean;
  last_commit?: { hash: string; short: string; subject: string; date: string } | null;
  has_remote?: boolean;
}

export interface GitRemote {
  name: string;
  fetch: string;
  push: string;
  fetch_urls: string[];
  push_urls: string[];
}

export interface GitRemoteSync {
  remote: string;
  branch: string;
  tracking_ref: string;
  remote_branch_exists: boolean;
  ahead: number;
  behind: number;
  diverged: boolean;
  can_fast_forward: boolean;
  state: "unpublished" | "up_to_date" | "ahead" | "behind" | "diverged";
}

// ------------------------------------------------------------------ system

export interface JobRecord {
  id: string;
  kind: string;
  project_id: string | null;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  progress: number;
  message: string;
  payload: Record<string, unknown>;
  result: Record<string, unknown>;
  error: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface EmbeddingBackendInfo {
  id: string;
  name: string;
  available: boolean;
  quality: string;
  portable: boolean;
  model: string;
  requirement: string;
  note: string;
  model_cached?: boolean;
  endpoint?: string;
  endpoint_reachable?: boolean | null;
  blocker?: string;
}

export interface HealthReport {
  ok: boolean;
  version: string;
  uptime_s: number;
  paths: Record<string, string>;
  dotenv: string | null;
  database: {
    path: string;
    size_bytes: number;
    schema_version: number;
    counts: Record<string, number | null>;
  };
  jobs: { active: number; recent: number };
  events: { subscribers: number };
  retrieval: {
    total: number;
    available: string[];
    unavailable: Record<string, string>;
    enabled: string[];
  };
  llm: {
    configured: string[];
    usable: string[];
    has_any: boolean;
    roles: Record<string, string>;
    supported_kinds: string[];
  };
  analysis: {
    embedding_backends: EmbeddingBackendInfo[];
    reducers: { id: string; name: string; available: boolean; supports_new_points: boolean; note: string }[];
    clusterers: { id: string; name: string; available: boolean; note: string }[];
    gap_detectors: { id: string; name: string; name_zh: string; strength: string; needs: string; explains: string }[];
    min_papers_for_full_analysis: number;
    optional_stack_installed: Record<string, boolean>;
  };
  export: {
    formats: { id: string; name: string; always_available: boolean; note: string }[];
    pandoc: boolean;
    latex_engines: Record<string, boolean>;
    can_build_pdf: boolean;
    document_classes: { id: string; name: string; latex_class: string; note?: string }[];
    citation_styles: string[];
  };
  git: { available: boolean; auto_commit: boolean };
  ui: {
    theme: "dark" | "light";
    accent: string;
    font_size: number;
    sidebar_width: number;
    locale: "zh-CN" | "en-US";
    quick_start_version: number;
  };
  identity_configured: boolean;
}

export interface ConfigurationSources {
  precedence: Array<"defaults" | "settings_file" | "secrets_file" | "dotenv" | "environment">;
  settings_file: { path: string; exists: boolean; fields: string[] };
  secrets_file: { path: string; exists: boolean; fields: string[] };
  dotenv: { path: string | null; variables: string[]; override_fields: string[] };
  environment: { variables: string[]; override_fields: string[] };
}

export interface PaperTemplate {
  id: string;
  name: string;
  name_zh: string;
  description: string;
  description_zh?: string;
  category?: string;
  source_kind?: string;
  license_note?: string;
  total_words: number;
  section_count: number;
  sections: { key: string; title: string; title_zh: string; target_words: number }[];
}

// ------------------------------------------------ assistant and prompt templates

export interface PromptTemplate {
  id: string;
  project_id: string | null;
  scope: "workbench" | "project";
  name: string;
  description: string;
  content: string;
  variables: string[];
  created_at: string;
  updated_at: string;
}

export interface AssistantAction {
  kind: "draft_skill" | "open_search" | "commit_local_version" | "insert_into_section";
  requires_confirmation: boolean;
  payload: Record<string, unknown>;
}

export interface AssistantChatResponse {
  answer: string;
  provider: string;
  model: string;
  usage: { prompt_tokens: number; completion_tokens: number; reported: boolean };
  used_skills: string[];
  skill_problems: string[];
  context: Record<string, unknown>;
  suggested_actions: AssistantAction[];
  thread_id: string;
}

export interface AssistantThread {
  id: string;
  project_id: string | null;
  title: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
  character_count?: number;
  estimated_bytes?: number;
  last_activity?: string;
}

export interface AssistantMessage {
  id: string;
  thread_id: string;
  ordering: number;
  role: "user" | "assistant";
  content: string;
  actions: AssistantAction[];
  meta: Record<string, unknown>;
  created_at: string;
}

export interface AssistantScopeStats {
  thread_count: number;
  message_count: number;
  user_messages?: number;
  assistant_messages?: number;
  character_count: number;
  estimated_bytes: number;
  first_activity: string | null;
  last_activity: string | null;
}

export interface AssistantConversationExport {
  format: "papercreator.assistant_conversations";
  format_version: 1;
  exported_at: string;
  scope: { kind: "workbench" | "project"; project_id: string | null; title: string };
  retention_days: number;
  stats: AssistantScopeStats;
  threads: Array<AssistantThread & { messages: AssistantMessage[] }>;
}

export interface AssistantMaintenancePreview {
  scope: { kind: "workbench" | "project"; project_id: string | null };
  mode: "all" | "retention";
  older_than_days: number;
  cutoff: string | null;
  preview_token: string;
  stats: AssistantScopeStats;
}

export interface AssistantImportPreview {
  target_scope: { kind: "workbench" | "project"; project_id: string | null };
  source_scope: { kind: "workbench" | "project"; project_id: string | null; title?: string };
  preview_token: string;
  stats: {
    thread_count: number;
    message_count: number;
    character_count: number;
    estimated_bytes: number;
    new_threads: number;
    already_imported_threads: number;
  };
}

export interface AssistantImportResult {
  target_scope: { kind: "workbench" | "project"; project_id: string | null };
  imported_threads: number;
  imported_messages: number;
  skipped_threads: number;
  thread_ids: string[];
}

export interface AssistantMessageRedactionPreview {
  preview_token: string;
  message_id: string;
  thread_id: string;
  thread_title: string;
  project_id: string | null;
  role: "user" | "assistant";
  created_at: string;
  character_count: number;
  estimated_bytes: number;
  actions_count: number;
  has_meta: boolean;
  original_sha256: string;
}

/**
 * Typed wrappers for every backend endpoint the UI uses.
 *
 * Views call these rather than `api.get("/api/...")` directly, so a route change
 * is a one-line edit here instead of a search across components, and so response
 * shapes are typed at exactly one place.
 */

import { api } from "./client";
import type {
  AgentPipeline,
  AgentRole,
  AgentRun,
  AgentEvaluationRequest,
  AgentEvaluationSummary,
  AgentReviewPacket,
  AnalysisDetail,
  AnalysisPaperRow,
  AnalysisSummary,
  ConfigurationSources,
  DocumentModel,
  GapCandidate,
  GitRemote,
  GitRemoteSync,
  GitStatus,
  HealthReport,
  JobRecord,
  ManuscriptSyncStatus,
  ManuscriptStats,
  Paper,
  PaperTemplate,
  PromptTemplate,
  AssistantChatResponse,
  AssistantConversationExport,
  AssistantImportPreview,
  AssistantImportResult,
  AssistantMaintenancePreview,
  AssistantMessage,
  AssistantMessageRedactionPreview,
  AssistantScopeStats,
  AssistantThread,
  PositionResult,
  Project,
  ProviderInfo,
  SearchResponse,
  Section,
  SkillDraft,
  SkillInfo,
  TimelineEntry,
  WorkbenchInfo,
  WorkbenchImportAccepted,
  WorkbenchImportResult,
  WorkbenchResource,
} from "./types";

// ------------------------------------------------------------------ system

export const system = {
  health: () => api.get<HealthReport>("/api/system/health"),
  capabilities: () => api.get<Record<string, unknown>>("/api/system/capabilities"),
  jobs: (query?: { project_id?: string; status?: string; limit?: number }) =>
    api.get<{ items: JobRecord[] }>("/api/system/jobs", query),
  job: (jobId: string) => api.get<JobRecord>(`/api/system/jobs/${jobId}`),
  cancelJob: (jobId: string) =>
    api.post<{ requested: boolean }>(`/api/system/jobs/${jobId}/cancel`),
  logs: (which: "main" | "errors" = "main", lines = 300) =>
    api.get<{ path: string; lines: string[]; exists: boolean }>("/api/system/logs", {
      which,
      lines,
    }),
  usage: (days = 30) => api.get<Record<string, unknown>>("/api/system/usage", { days }),
  cache: () => api.get<Record<string, unknown>>("/api/system/cache"),
  maintenance: (body: Record<string, unknown>) =>
    api.post<Record<string, unknown>>("/api/system/maintenance", body),
};

// ------------------------------------------------ assistant and prompts

export const assistant = {
  threads: (projectId = "") =>
    api.get<{ items: AssistantThread[]; stats: AssistantScopeStats }>(
      "/api/assistant/threads",
      { project_id: projectId },
    ),
  createThread: (projectId = "", title = "") =>
    api.post<{ thread: AssistantThread }>("/api/assistant/threads", {
      project_id: projectId,
      title,
    }),
  thread: (threadId: string) =>
    api.get<{ thread: AssistantThread; messages: AssistantMessage[] }>(
      `/api/assistant/threads/${threadId}`,
    ),
  deleteThread: (threadId: string) =>
    api.delete<{ deleted: boolean; id: string }>(`/api/assistant/threads/${threadId}`),
  exportThreads: (projectId = "") =>
    api.get<AssistantConversationExport>("/api/assistant/threads/export", {
      project_id: projectId,
    }),
  previewMaintenance: (
    projectId: string,
    mode: "all" | "retention",
    olderThanDays = 0,
  ) => api.post<AssistantMaintenancePreview>("/api/assistant/threads/maintenance/preview", {
    project_id: projectId,
    mode,
    older_than_days: olderThanDays,
  }),
  executeMaintenance: (preview: AssistantMaintenancePreview) =>
    api.post<{
      deleted_threads: number;
      deleted_messages: number;
      scope: { kind: "workbench" | "project"; project_id: string | null };
      mode: "all" | "retention";
      cutoff: string | null;
    }>("/api/assistant/threads/maintenance/execute", {
      project_id: preview.scope.project_id ?? "",
      mode: preview.mode,
      cutoff: preview.cutoff,
      preview_token: preview.preview_token,
      confirm: true,
    }),
  previewImport: (projectId: string, archive: AssistantConversationExport) =>
    api.post<AssistantImportPreview>("/api/assistant/threads/import/preview", {
      project_id: projectId,
      archive,
    }),
  executeImport: (
    projectId: string,
    archive: AssistantConversationExport,
    preview: AssistantImportPreview,
  ) => api.post<AssistantImportResult>("/api/assistant/threads/import/execute", {
    project_id: projectId,
    archive,
    preview_token: preview.preview_token,
    confirm: true,
  }),
  previewMessageRedaction: (messageId: string) =>
    api.post<AssistantMessageRedactionPreview>(
      `/api/assistant/messages/${messageId}/redaction/preview`,
    ),
  executeMessageRedaction: (
    messageId: string,
    preview: AssistantMessageRedactionPreview,
    reason: string,
  ) => api.post<{
    message_id: string;
    thread_id: string;
    redacted_at: string;
    audit: Record<string, unknown>;
  }>(`/api/assistant/messages/${messageId}/redaction/execute`, {
    preview_token: preview.preview_token,
    reason,
    confirm: true,
  }),
  chat: (body: {
    message: string;
    project_id?: string;
    section_key?: string;
    history?: { role: "user" | "assistant"; content: string }[];
    skill_ids?: string[];
    locale?: "zh-CN" | "en-US";
    model?: string;
    thread_id?: string;
  }) => api.post<AssistantChatResponse>("/api/assistant/chat", body),
};

export const prompts = {
  list: (projectId = "") =>
    api.get<{ items: PromptTemplate[] }>("/api/prompts", { project_id: projectId }),
  create: (body: Record<string, unknown>) =>
    api.post<{ template: PromptTemplate }>("/api/prompts", body),
  update: (templateId: string, body: Record<string, unknown>) =>
    api.put<{ template: PromptTemplate }>(`/api/prompts/${templateId}`, body),
  remove: (templateId: string) =>
    api.delete<{ deleted: boolean; id: string }>(`/api/prompts/${templateId}`),
};

// --------------------------------------------------------------- workbench

export const workbench = {
  info: () => api.get<WorkbenchInfo>("/api/workbench"),
  resources: (query?: { kind?: string; project_id?: string; limit?: number }) =>
    api.get<{ items: WorkbenchResource[]; total: number }>(
      "/api/workbench/resources",
      query,
    ),
  importResource: (body: Record<string, unknown>) =>
    api.post<WorkbenchImportResult>("/api/workbench/resources", body),
  startDirectoryImport: (body: Record<string, unknown>) =>
    api.post<WorkbenchImportAccepted>("/api/workbench/resources/import", body),
  removeResource: (resourceId: string, removeFiles = false) =>
    api.delete<Record<string, unknown>>(`/api/workbench/resources/${resourceId}`, {
      remove_files: removeFiles,
    }),
  updateState: (lastProjectId: string) =>
    api.patch<{ last_project_id: string }>("/api/workbench/state", {
      last_project_id: lastProjectId,
    }),
};

// ---------------------------------------------------------------- settings

export const settings = {
  read: () => api.get<Record<string, any>>("/api/settings"),
  sources: () => api.get<ConfigurationSources>("/api/settings/sources"),
  update: (patch: Record<string, unknown>) =>
    api.patch<Record<string, any>>("/api/settings", patch),
  reload: () => api.post<Record<string, any>>("/api/settings/reload"),
  deleteSecret: (path: string) =>
    api.delete<Record<string, any>>("/api/settings/secret", { path }),
  llmProviders: (probe = false) =>
    api.get<{ providers: any[]; roles: Record<string, string>; supported_kinds: string[] }>(
      "/api/settings/llm/providers",
      { probe },
    ),
  testLlm: (provider: string, model = "") =>
    api.post<{
      ok: boolean;
      reply?: string;
      error?: string;
      duration_ms: number;
      outcome?: string;
      error_code?: string;
      retryable?: boolean;
      http_status?: number | null;
      retry_after_s?: number | null;
      hint?: string;
    }>(
      "/api/settings/llm/test",
      { provider, model },
    ),
  upsertLlmProvider: (providerId: string, body: Record<string, unknown>) =>
    api.put<Record<string, any>>(`/api/settings/llm/providers/${providerId}`, body),
  retrievalProviders: () =>
    api.get<{ providers: ProviderInfo[]; enabled: string[] }>(
      "/api/settings/retrieval/providers",
    ),
  setEnabledProviders: (providerIds: string[]) =>
    api.put<Record<string, any>>("/api/settings/retrieval/enabled", {
      provider_ids: providerIds,
    }),
  analysisBackends: () => api.get<Record<string, any>>("/api/settings/analysis/backends"),
  probeModelHost: () =>
    api.post<{ endpoint: string; reachable: boolean; model_cached: boolean; blocker: string; hint: string }>(
      "/api/settings/analysis/probe-model-host",
    ),
  overleaf: () => api.get<Record<string, any>>("/api/settings/overleaf"),
};

// ---------------------------------------------------------------- projects

export const projects = {
  list: (status = "") =>
    api.get<{ items: Project[]; importable: any[] }>("/api/projects", { status }),
  create: (body: Record<string, unknown>) =>
    api.post<{ project: Project; document?: any; git?: any }>("/api/projects", body),
  get: (projectId: string) =>
    api.get<{
      project: Project;
      document: DocumentModel;
      collections: any[];
      stats: ManuscriptStats;
      bilingual: Record<string, any>;
      analyses: AnalysisSummary[];
      latest_analysis_id: string;
      git: GitStatus;
    }>(`/api/projects/${projectId}`),
  update: (projectId: string, patch: Record<string, unknown>) =>
    api.patch<{ project: Project }>(`/api/projects/${projectId}`, patch),
  remove: (projectId: string, removeFiles = false) =>
    api.delete<Record<string, any>>(`/api/projects/${projectId}`, {
      remove_files: removeFiles,
    }),
  relocate: (projectId: string, path: string) =>
    api.post<{ project: Project }>(`/api/projects/${projectId}/relocate`, { path }),
  importFromDisk: (path: string, reindex = true) =>
    api.post<Record<string, any>>("/api/projects/import", { path, reindex }),
  collections: (projectId: string) =>
    api.get<{ items: any[] }>(`/api/projects/${projectId}/collections`),
  createCollection: (projectId: string, name: string, kind = "manual") =>
    api.post<Record<string, any>>(`/api/projects/${projectId}/collections`, { name, kind }),
  addPapers: (projectId: string, collectionId: string, paperIds: string[]) =>
    api.post<{ added: number }>(
      `/api/projects/${projectId}/collections/${collectionId}/papers`,
      { paper_ids: paperIds },
    ),
  removePapers: (projectId: string, collectionId: string, paperIds: string[]) =>
    api.delete<{ removed: number }>(
      `/api/projects/${projectId}/collections/${collectionId}/papers`,
      undefined,
      { paper_ids: paperIds },
    ),
  papers: (projectId: string, query?: Record<string, string | number>) =>
    api.get<{ items: Paper[]; total: number }>(`/api/projects/${projectId}/papers`, query),
};

// ------------------------------------------------------------------ search

export const search = {
  providers: () => api.get<{ providers: ProviderInfo[] }>("/api/search/providers"),
  submit: (body: Record<string, unknown>) =>
    api.post<{ job_id: string; mode: string }>("/api/search", body),
  sync: (body: Record<string, unknown>) =>
    api.post<SearchResponse>("/api/search/sync", body),
  expand: (body: { query?: string; seed_text?: string; use_llm?: boolean }) =>
    api.post<Record<string, any>>("/api/search/expand", body),
  resolve: (identifier: string, addToProject = "") =>
    api.post<{ paper: Paper }>("/api/search/resolve", {
      identifier,
      add_to_project: addToProject,
    }),
  history: (projectId = "", limit = 50) =>
    api.get<{ items: any[] }>("/api/search/history", { project_id: projectId, limit }),
  getSearch: (searchId: string) =>
    api.get<Record<string, any>>(`/api/search/history/${searchId}`),
  rerun: (searchId: string, useCache = false) =>
    api.post<{ job_id: string }>(`/api/search/history/${searchId}/rerun`, undefined, {
      use_cache: useCache,
    }),
};

// ----------------------------------------------------------------- library

export const library = {
  browse: (query?: Record<string, string | number | boolean>) =>
    api.get<{ items: Paper[]; total: number; limit: number; offset: number }>(
      "/api/library",
      query,
    ),
  stats: () => api.get<{ library: Record<string, any>; tags: any[] }>("/api/library/stats"),
  get: (paperId: string) => api.get<Paper>(`/api/library/${paperId}`),
  update: (paperId: string, patch: Record<string, unknown>) =>
    api.patch<Paper>(`/api/library/${paperId}`, patch),
  remove: (paperId: string) => api.delete<Record<string, any>>(`/api/library/${paperId}`),
  removeMany: (paperIds: string[]) =>
    api.post<{ deleted: number }>("/api/library/delete", { paper_ids: paperIds }),
  tag: (paperIds: string[], add: string[] = [], remove: string[] = []) =>
    api.post<{ updated: number }>("/api/library/tag", { paper_ids: paperIds, add, remove }),
  addPaper: (body: Record<string, unknown>) =>
    api.post<{ paper: Paper }>("/api/library/papers", body),
  importFile: (path: string, projectId = "", collectionName = "") =>
    api.post<Record<string, any>>("/api/library/import", {
      path,
      project_id: projectId,
      collection_name: collectionName,
    }),
  duplicates: (projectId = "", threshold = 0.92) =>
    api.get<{ scanned: number; groups: any[] }>("/api/library/duplicates", {
      project_id: projectId,
      threshold,
    }),
  merge: (keepId: string, mergeIds: string[]) =>
    api.post<Record<string, any>>("/api/library/merge", {
      keep_id: keepId,
      merge_ids: mergeIds,
    }),
  downloadPdfs: (paperIds: string[]) =>
    api.post<{ job_id: string }>("/api/library/download-pdfs", { paper_ids: paperIds }),
};

// ---------------------------------------------------------------- analysis

export const analysis = {
  capabilities: () => api.get<Record<string, any>>("/api/analysis/capabilities"),
  submit: (body: Record<string, unknown>) =>
    api.post<{ job_id: string }>("/api/analysis", body),
  sync: (body: Record<string, unknown>) =>
    api.post<AnalysisSummary>("/api/analysis/sync", body),
  list: (projectId = "", limit = 50) =>
    api.get<{ items: AnalysisSummary[] }>("/api/analysis", {
      project_id: projectId,
      limit,
    }),
  get: (
    analysisId: string,
    options: {
      include_points?: boolean;
      include_heatmap?: boolean;
      include_layers?: boolean;
      include_papers?: boolean;
    } = {},
  ) => api.get<AnalysisDetail>(`/api/analysis/${analysisId}`, options),
  layer: (analysisId: string, term: string) =>
    api.get<{ term: string; grid: number[][]; grid_size: number; bounds: number[] }>(
      `/api/analysis/${analysisId}/layer/${encodeURIComponent(term)}`,
    ),
  papers: (analysisId: string) =>
    api.get<{ items: AnalysisPaperRow[] }>(`/api/analysis/${analysisId}/papers`),
  remove: (analysisId: string) =>
    api.delete<Record<string, any>>(`/api/analysis/${analysisId}`),
  placeIdea: (
    analysisId: string,
    body: { title: string; abstract?: string; keywords?: string[]; project_id?: string },
  ) => api.post<PositionResult>(`/api/analysis/${analysisId}/place-idea`, body),
  placePaper: (analysisId: string, paperId: string) =>
    api.post<PositionResult>(`/api/analysis/${analysisId}/place-paper`, {
      paper_id: paperId,
    }),
  removePoints: (analysisId: string, paperIds: string[]) =>
    api.post<Record<string, any>>(`/api/analysis/${analysisId}/remove-points`, {
      paper_ids: paperIds,
    }),
  graph: (analysisId: string) =>
    api.get<Record<string, any>>(`/api/analysis/${analysisId}/graph`),
  projectGraph: (projectId: string) =>
    api.get<Record<string, any>>(`/api/analysis/project/${projectId}/graph`),
  labelClusters: (analysisId: string, clusterIds: number[] = []) =>
    api.post<{ updated: number; clusters: any[] }>(
      `/api/analysis/${analysisId}/label-clusters`,
      { cluster_ids: clusterIds },
    ),
};

// ----------------------------------------------------------------- writing

export const writing = {
  templates: () => api.get<{ items: PaperTemplate[] }>("/api/writing/templates"),
  translationProviders: () =>
    api.get<{ items: Array<Record<string, any>> }>("/api/writing/translation/providers"),
  translate: (body: Record<string, unknown>) =>
    api.post<{ text: string; provider: string; source: string; target: string; note: string; found?: boolean }>(
      "/api/writing/translate",
      body,
    ),
  startTranslationJob: (body: Record<string, unknown>) =>
    api.post<{
      job_id: string;
      status: "queued";
      mode: "text" | "project";
      sections: number;
      source_characters: number;
      preview_only: true;
    }>("/api/writing/translation/jobs", body),
  applyTranslationJob: (jobId: string) =>
    api.post<{
      applied: boolean;
      job_id: string;
      project_id: string;
      sections_applied: number;
      snapshot_id: string;
      applied_at: string;
      already_applied: boolean;
    }>(`/api/writing/translation/jobs/${jobId}/apply`, { confirm: true }),
  previewImport: (
    sourcePath: string,
    projectId = "",
    options?: { use_ocr?: boolean; ocr_languages?: string; ocr_max_pages?: number },
  ) =>
    api.post<Record<string, any>>("/api/writing/import/preview", {
      source_path: sourcePath,
      project_id: projectId,
      ...options,
    }),
  ocrCapabilities: () => api.get<Record<string, any>>("/api/writing/import/ocr-capabilities"),
  applyImport: (projectId: string, body: Record<string, unknown>) =>
    api.post<Record<string, any>>(`/api/writing/${projectId}/import`, body),
  previewVenueTemplate: (sourcePath: string) =>
    api.post<Record<string, any>>("/api/writing/venue-template/preview", {
      source_path: sourcePath,
    }),
  importVenueTemplate: (projectId: string, body: Record<string, unknown>) =>
    api.post<Record<string, any>>(`/api/writing/${projectId}/venue-template`, body),
  applyTemplate: (
    projectId: string,
    body: { template_id?: string; target_words?: number; replace?: boolean },
  ) =>
    api.post<{ document: DocumentModel; stats: ManuscriptStats }>(
      `/api/writing/${projectId}/template`,
      body,
    ),
  document: (projectId: string, includeContent = true) =>
    api.get<{ document: DocumentModel; stats: ManuscriptStats }>(
      `/api/writing/${projectId}/document`,
      { include_content: includeContent },
    ),
  section: (projectId: string, key: string) =>
    api.get<Section>(`/api/writing/${projectId}/sections/${key}`),
  updateSection: (projectId: string, key: string, patch: Record<string, unknown>) =>
    api.patch<{ section: Section }>(`/api/writing/${projectId}/sections/${key}`, patch),
  createSection: (projectId: string, body: Record<string, unknown>) =>
    api.post<{ section: Section }>(`/api/writing/${projectId}/sections`, body),
  deleteSection: (projectId: string, key: string) =>
    api.delete<Record<string, any>>(`/api/writing/${projectId}/sections/${key}`),
  reorder: (projectId: string, sectionKeys: string[]) =>
    api.post<{ sections: Section[] }>(`/api/writing/${projectId}/reorder`, {
      section_keys: sectionKeys,
    }),
  stats: (projectId: string) => api.get<ManuscriptStats>(`/api/writing/${projectId}/stats`),
  bilingual: (projectId: string) =>
    api.get<Record<string, any>>(`/api/writing/${projectId}/bilingual`),
  swapLanguages: (projectId: string) =>
    api.post<Record<string, any>>(`/api/writing/${projectId}/swap-languages`),
  assembled: (projectId: string, language: "primary" | "paired" = "primary") =>
    api.get<{ text: string; word_count: number; blocks: any[] }>(
      `/api/writing/${projectId}/assembled`,
      { language },
    ),
  bibliography: (projectId: string, citedOnly = true) =>
    api.post<Record<string, any>>(`/api/writing/${projectId}/bibliography`, undefined, {
      cited_only: citedOnly,
    }),
  syncStatus: (projectId: string) =>
    api.get<ManuscriptSyncStatus>(`/api/writing/${projectId}/sync-status`),
  flush: (projectId: string, force = false) =>
    api.post<Record<string, any>>(
      `/api/writing/${projectId}/flush`,
      undefined,
      { force },
    ),
  reindex: (projectId: string, force = false) =>
    api.post<Record<string, any>>(
      `/api/writing/${projectId}/reindex`,
      undefined,
      { force },
    ),
  mergeDisjoint: (projectId: string, previewToken: string) =>
    api.post<{
      merged_from_database: string[];
      merged_from_disk: string[];
      sync: ManuscriptSyncStatus;
      safety_backups: Array<{ path: string; side: "database" | "disk" }>;
      safety_snapshot: { id: string };
    }>(`/api/writing/${projectId}/merge-disjoint`, {
      preview_token: previewToken,
      confirm: true,
    }),
};

// ------------------------------------------------------------------ agents

export const agents = {
  pipelines: () =>
    api.get<{ pipelines: AgentPipeline[]; roles: AgentRole[] }>("/api/agents/pipelines"),
  run: (body: Record<string, unknown>) =>
    api.post<{ job_id: string; run_id: string; pipeline: string }>("/api/agents/run", body),
  runs: (projectId = "", limit = 50) =>
    api.get<{ items: AgentRun[] }>("/api/agents/runs", { project_id: projectId, limit }),
  getRun: (runId: string, includePrompts = false) =>
    api.get<AgentRun>(`/api/agents/runs/${runId}`, { include_prompts: includePrompts }),
  step: (runId: string, stepId: string) =>
    api.get<Record<string, any>>(`/api/agents/runs/${runId}/steps/${stepId}`),
  cancel: (runId: string) =>
    api.post<{ requested: boolean }>(`/api/agents/runs/${runId}/cancel`),
  evaluate: (runId: string, body: AgentEvaluationRequest) =>
    api.post<AgentRun>(`/api/agents/runs/${runId}/evaluations`, body),
  evaluationSummary: (projectId = "", limit = 500) =>
    api.get<AgentEvaluationSummary>("/api/agents/evaluations/summary", {
      project_id: projectId,
      limit,
    }),
  reviewPacket: (runId: string, kind: "blind" | "analysis" = "blind") =>
    api.get<AgentReviewPacket>(`/api/agents/runs/${runId}/review-packet`, { kind }),
  exportReviewPacket: (runId: string, kind: "blind" | "analysis" = "blind") =>
    api.post<{
      path: string;
      packet_kind: "blind" | "analysis";
      sample_id: string;
      packet_fingerprint: string;
      bytes: number;
    }>(`/api/agents/runs/${runId}/review-packet/export`, { kind }),
  removeRun: (runId: string) => api.delete<Record<string, any>>(`/api/agents/runs/${runId}`),
  preview: (body: Record<string, unknown>) =>
    api.post<Record<string, any>>("/api/agents/preview", body),
  blackboard: (projectId: string, analysisId = "") =>
    api.get<Record<string, any>>(`/api/agents/blackboard/${projectId}`, {
      analysis_id: analysisId,
    }),
};

// ------------------------------------------------------------------ skills

export const skills = {
  list: (projectId = "") =>
    api.get<{ items: SkillInfo[]; stats: Record<string, any>; directories: Record<string, string>; valid_roles: string[] }>(
      "/api/skills",
      { project_id: projectId },
    ),
  sync: (projectId = "") =>
    api.post<Record<string, any>>("/api/skills/sync", undefined, { project_id: projectId }),
  get: (skillId: string, projectId = "") =>
    api.get<Record<string, any>>(`/api/skills/${skillId}`, { project_id: projectId }),
  save: (body: Record<string, unknown>) =>
    api.post<Record<string, any>>("/api/skills", body),
  draft: (request: string, existingSkillId = "") =>
    api.post<{ draft: SkillDraft }>("/api/skills/draft", {
      request,
      existing_skill_id: existingSkillId,
    }),
  setEnabled: (skillId: string, enabled: boolean) =>
    api.post<Record<string, any>>(`/api/skills/${skillId}/enabled`, undefined, { enabled }),
  remove: (skillId: string) => api.delete<Record<string, any>>(`/api/skills/${skillId}`),
  copy: (skillId: string, newId = "") =>
    api.post<Record<string, any>>(`/api/skills/${skillId}/copy`, { new_id: newId }),
  importFrom: (path: string) => api.post<Record<string, any>>("/api/skills/import", { path }),
  preview: (skillIds: string[], role = "", projectId = "") =>
    api.post<{ text: string; used: string[]; problems: string[]; estimated_tokens: number; budget_tokens: number }>(
      "/api/skills/preview",
      { skill_ids: skillIds, role, project_id: projectId },
    ),
  suggest: (text: string, projectId = "") =>
    api.post<{ suggestions: any[] }>("/api/skills/suggest", {
      text,
      project_id: projectId,
    }),
};

// ------------------------------------------------------------------ export

export const exports = {
  capabilities: () => api.get<Record<string, any>>("/api/export/capabilities"),
  run: (projectId: string, body: Record<string, unknown>) =>
    api.post<Record<string, any>>(`/api/export/${projectId}`, body),
  pdf: (projectId: string, documentClass = "article") =>
    api.post<Record<string, any>>(`/api/export/${projectId}/pdf`, undefined, {
      document_class: documentClass,
    }),
  files: (projectId: string) =>
    api.get<{ directory: string; items: any[] }>(`/api/export/${projectId}/files`),
  downloadUrl: (projectId: string, path: string) =>
    api.url(`/api/export/${projectId}/download`, { path }),
  overleafStatus: (projectId: string) =>
    api.get<Record<string, any>>(`/api/export/${projectId}/overleaf/status`),
  overleafZip: (projectId: string, documentClass = "article") =>
    api.post<Record<string, any>>(`/api/export/${projectId}/overleaf/zip`, {
      document_class: documentClass,
    }),
  overleafPush: (projectId: string, body: Record<string, unknown>) =>
    api.post<Record<string, any>>(`/api/export/${projectId}/overleaf/push`, body),
  overleafPull: (projectId: string, apply = false) =>
    api.post<Record<string, any>>(`/api/export/${projectId}/overleaf/pull`, undefined, {
      apply_to_manuscript: apply,
    }),
  convert: (text: string, direction: "md2tex" | "tex2md") =>
    api.post<{ result: string; note?: string }>("/api/export/convert", { text, direction }),
};

// ---------------------------------------------------------------- versions

export const versions = {
  timeline: (projectId: string, limit = 100) =>
    api.get<{ entries: TimelineEntry[]; git: Record<string, any>; counts: Record<string, number> }>(
      `/api/versions/${projectId}`,
      { limit },
    ),
  gitStatus: (projectId: string) =>
    api.get<GitStatus>(`/api/versions/${projectId}/git/status`),
  gitInit: (projectId: string) =>
    api.post<Record<string, any>>(`/api/versions/${projectId}/git/init`),
  commit: (projectId: string, message: string, flushFirst = true) =>
    api.post<Record<string, any>>(`/api/versions/${projectId}/git/commit`, {
      message,
      flush_first: flushFirst,
    }),
  log: (projectId: string, limit = 50, path = "") =>
    api.get<{ entries: any[] }>(`/api/versions/${projectId}/git/log`, { limit, path }),
  diff: (projectId: string, ref = "", path = "") =>
    api.get<{ diff: string; stat: string }>(`/api/versions/${projectId}/git/diff`, {
      ref,
      path,
    }),
  branches: (projectId: string) =>
    api.get<{ branches: any[]; current: string }>(`/api/versions/${projectId}/git/branches`),
  createBranch: (projectId: string, name: string) =>
    api.post<Record<string, any>>(`/api/versions/${projectId}/git/branch`, { name }),
  checkout: (projectId: string, ref: string) =>
    api.post<Record<string, any>>(`/api/versions/${projectId}/git/checkout`, { ref }),
  discard: (projectId: string, confirm = false) =>
    api.post<Record<string, any>>(
      `/api/versions/${projectId}/git/discard`,
      undefined,
      { confirm },
    ),
  remotes: (projectId: string) =>
    api.get<{ remotes: GitRemote[] }>(`/api/versions/${projectId}/git/remotes`),
  fetchRemote: (projectId: string, remote = "origin") =>
    api.post<{ fetched: boolean; output: string; sync: GitRemoteSync }>(
      `/api/versions/${projectId}/git/fetch`,
      undefined,
      { remote },
    ),
  pull: (projectId: string, remote = "origin") =>
    api.post<Record<string, any>>(
      `/api/versions/${projectId}/git/pull`,
      undefined,
      { remote },
    ),
  push: (projectId: string, remote = "origin") =>
    api.post<Record<string, any>>(
      `/api/versions/${projectId}/git/push`,
      undefined,
      { remote },
    ),
  setRemote: (projectId: string, url: string, name = "origin") =>
    api.post<Record<string, any>>(`/api/versions/${projectId}/git/remote`, { url, name }),
  removeRemote: (projectId: string, name = "origin") =>
    api.delete<Record<string, any>>(`/api/versions/${projectId}/git/remote`, { name }),
  snapshots: (projectId: string) =>
    api.get<{ items: any[] }>(`/api/versions/${projectId}/snapshots`),
  createSnapshot: (projectId: string, label = "") =>
    api.post<Record<string, any>>(`/api/versions/${projectId}/snapshots`, { label }),
  deleteSnapshot: (projectId: string, snapshotId: string) =>
    api.delete<Record<string, any>>(`/api/versions/${projectId}/snapshots/${snapshotId}`),
  save: (projectId: string, body: Record<string, unknown>) =>
    api.post<Record<string, any>>(`/api/versions/${projectId}/save`, body),
  compare: (projectId: string, left: string, right = "current", language = "primary") =>
    api.get<Record<string, any>>(`/api/versions/${projectId}/compare`, {
      left,
      right,
      language,
    }),
  restore: (projectId: string, ref: string, sectionKeys: string[] = []) =>
    api.post<Record<string, any>>(`/api/versions/${projectId}/restore`, {
      ref,
      section_keys: sectionKeys,
    }),
  sectionHistory: (projectId: string, key: string) =>
    api.get<Record<string, any>>(`/api/versions/${projectId}/sections/${key}/history`),
  sectionAt: (projectId: string, key: string, ref: string) =>
    api.get<Record<string, any>>(`/api/versions/${projectId}/sections/${key}/at`, { ref }),
};

export type GapCandidateType = GapCandidate;

/**
 * Application state (Zustand).
 *
 * One store rather than several, because most of the interesting state is
 * cross-cutting: opening a project reloads the document, the library filter, the
 * analysis list and the git status together, and a job event can touch any of
 * them.
 *
 * Server data is cached here rather than refetched per render, and invalidated by
 * SSE events. That is what makes the agent view update live while a run writes
 * sections.
 */

import { create } from "zustand";

import { ApiError } from "../api/client";
import * as endpoints from "../api/endpoints";
import * as events from "../api/events";
import type {
  AgentRun,
  AnalysisDetail,
  AnalysisSummary,
  DocumentModel,
  GitStatus,
  HealthReport,
  JobRecord,
  ManuscriptStats,
  Paper,
  Project,
  ProviderInfo,
  ProviderStats,
  SearchResponse,
  SkillInfo,
  TimelineEntry,
  WorkbenchInfo,
  WorkbenchResource,
} from "../api/types";

export type ViewId =
  | "projects"
  | "search"
  | "library"
  | "landscape"
  | "editor"
  | "agents"
  | "versions"
  | "export"
  | "skills"
  | "settings"
  | "output";

export interface Toast {
  id: string;
  kind: "info" | "success" | "warning" | "error";
  message: string;
  detail?: string;
  /** Set for actionable errors so the toast can offer a fix. */
  action?: { label: string; view: ViewId };
}

interface StoreState {
  // ------------------------------------------------------------- lifecycle
  booted: boolean;
  backendReady: boolean;
  backendError: string;
  health: HealthReport | null;
  connection: events.ConnectionState;
  workbench: WorkbenchInfo | null;
  workbenchResources: WorkbenchResource[];

  // ------------------------------------------------------------ navigation
  view: ViewId;
  paletteOpen: boolean;
  quickStartOpen: boolean;
  projectCreatorOpen: boolean;
  sidebarWidth: number;
  panelOpen: boolean;
  assistantOpen: boolean;
  locale: "zh-CN" | "en-US";

  // -------------------------------------------------------------- projects
  projects: Project[];
  importable: Record<string, unknown>[];
  activeProjectId: string;
  project: Project | null;
  document: DocumentModel | null;
  stats: ManuscriptStats | null;
  git: GitStatus | null;
  activeSectionKey: string;
  /** Section text edited but not yet saved, keyed by section key. */
  dirtySections: Record<string, string>;

  // --------------------------------------------------------------- library
  providers: ProviderInfo[];
  searchResult: SearchResponse | null;
  searchRunning: boolean;
  searchProgress: Array<
    Pick<ProviderStats, "provider" | "count" | "outcome" | "error" | "retryable" | "hint">
  >;
  libraryPapers: Paper[];
  libraryTotal: number;
  selectedPaperIds: string[];

  // -------------------------------------------------------------- analysis
  analyses: AnalysisSummary[];
  analysis: AnalysisDetail | null;
  analysisLoading: boolean;
  activeLayer: string;
  highlightedClusters: number[];
  selectedGapId: string;

  // ---------------------------------------------------------------- agents
  runs: AgentRun[];
  activeRun: AgentRun | null;
  /** Live token deltas per section while a writer agent streams. */
  streaming: Record<string, string>;

  // ---------------------------------------------------------------- others
  skills: SkillInfo[];
  enabledSkillIds: string[];
  timeline: TimelineEntry[];
  jobs: JobRecord[];
  toasts: Toast[];
  backendLog: { ts: number; stream: string; text: string }[];

  // ----------------------------------------------------------------- verbs
  boot: () => Promise<void>;
  refreshHealth: () => Promise<void>;
  loadWorkbench: () => Promise<void>;
  loadWorkbenchResources: (kind?: string) => Promise<void>;
  setView: (view: ViewId) => void;
  togglePalette: (open?: boolean) => void;
  openQuickStart: () => void;
  closeQuickStart: () => void;
  dismissQuickStart: () => Promise<void>;
  openProjectCreator: () => void;
  closeProjectCreator: () => void;
  togglePanel: (open?: boolean) => void;
  toggleAssistant: (open?: boolean) => void;
  setSidebarWidth: (width: number) => void;
  setLocale: (locale: "zh-CN" | "en-US") => Promise<void>;
  notify: (toast: Omit<Toast, "id">) => void;
  dismissToast: (id: string) => void;
  reportError: (error: unknown, context?: string) => void;

  loadProjects: () => Promise<void>;
  openProject: (projectId: string) => Promise<void>;
  closeProject: () => void;
  reloadDocument: () => Promise<void>;
  setActiveSection: (key: string) => void;
  editSection: (key: string, content: string) => void;
  saveSection: (key: string) => Promise<void>;
  saveAllSections: () => Promise<void>;

  loadProviders: () => Promise<void>;
  runSearch: (body: Record<string, unknown>) => Promise<void>;
  loadLibrary: (query?: Record<string, string | number | boolean>) => Promise<void>;
  togglePaperSelected: (paperId: string) => void;
  clearSelection: () => void;

  loadAnalyses: () => Promise<void>;
  openAnalysis: (analysisId: string) => Promise<void>;
  buildAnalysis: (overrides?: Record<string, unknown>) => Promise<void>;
  setActiveLayer: (term: string) => Promise<void>;
  toggleClusterHighlight: (clusterId: number) => void;
  selectGap: (gapId: string) => void;

  loadRuns: () => Promise<void>;
  startAgentRun: (body: Record<string, unknown>) => Promise<void>;
  openRun: (runId: string) => Promise<void>;
  cancelRun: (runId: string) => Promise<void>;

  loadSkills: () => Promise<void>;
  toggleSkill: (skillId: string) => void;
  loadTimeline: () => Promise<void>;
  loadJobs: () => Promise<void>;
  appendBackendLog: (line: { stream: string; text: string }) => void;
}

let toastCounter = 0;
let quickStartAutoOffered = false;

export const useStore = create<StoreState>((set, get) => ({
  booted: false,
  backendReady: false,
  backendError: "",
  health: null,
  connection: "closed",
  workbench: null,
  workbenchResources: [],

  view: "projects",
  paletteOpen: false,
  quickStartOpen: false,
  projectCreatorOpen: false,
  sidebarWidth: 320,
  panelOpen: false,
  assistantOpen: true,
  locale: "zh-CN",

  projects: [],
  importable: [],
  activeProjectId: "",
  project: null,
  document: null,
  stats: null,
  git: null,
  activeSectionKey: "",
  dirtySections: {},

  providers: [],
  searchResult: null,
  searchRunning: false,
  searchProgress: [],
  libraryPapers: [],
  libraryTotal: 0,
  selectedPaperIds: [],

  analyses: [],
  analysis: null,
  analysisLoading: false,
  activeLayer: "",
  highlightedClusters: [],
  selectedGapId: "",

  runs: [],
  activeRun: null,
  streaming: {},

  skills: [],
  enabledSkillIds: [],
  timeline: [],
  jobs: [],
  toasts: [],
  backendLog: [],

  // ------------------------------------------------------------------ boot
  async boot() {
    try {
      const health = await endpoints.system.health();
      set({ health, backendReady: true, backendError: "", booted: true });
      // The backend's saved UI locale wins over the default.
      const locale = health.ui?.locale;
      if (locale === "en-US" || locale === "zh-CN") set({ locale });
    } catch (error) {
      set({
        booted: true,
        backendReady: false,
        backendError:
          error instanceof ApiError ? error.message : String(error),
      });
      return;
    }

    events.onConnectionChange((connection) => set({ connection }));
    subscribeToEvents(set, get);
    events.connect();

    await Promise.allSettled([
      get().loadWorkbench(),
      get().loadProjects(),
      get().loadProviders(),
      get().loadSkills(),
    ]);

    // Reopen whatever was last used, so restarting the app resumes work.
    const remembered = get().workbench?.last_project_id;
    if (remembered && get().projects.some((p) => p.id === remembered)) {
      await get().openProject(remembered);
    }

    // Offer the guide only for a genuinely empty workbench. Existing users can
    // always reopen it from Help or the command palette without an interruption.
    const hasExistingWork =
      (get().workbench?.project_count ?? 0) > 0 ||
      (get().workbench?.categories ?? []).some((category) => category.count > 0);
    if (
      !quickStartAutoOffered &&
      (get().health?.ui?.quick_start_version ?? 0) < 1 &&
      !hasExistingWork
    ) {
      quickStartAutoOffered = true;
      set({ quickStartOpen: true });
    }
  },

  async refreshHealth() {
    try {
      set({ health: await endpoints.system.health(), backendReady: true });
    } catch (error) {
      set({ backendReady: false });
      get().reportError(error, "health check");
    }
  },

  async loadWorkbench() {
    try {
      set({ workbench: await endpoints.workbench.info() });
    } catch (error) {
      get().reportError(error, "loading the workbench");
    }
  },

  async loadWorkbenchResources(kind = "") {
    try {
      const { items } = await endpoints.workbench.resources({ kind, limit: 1000 });
      set({ workbenchResources: items });
    } catch (error) {
      get().reportError(error, "loading workbench resources");
    }
  },

  setView(view) {
    set({ view, paletteOpen: false });
  },
  togglePalette(open) {
    set((state) => ({ paletteOpen: open ?? !state.paletteOpen }));
  },
  openQuickStart() {
    set({ quickStartOpen: true, paletteOpen: false });
  },
  closeQuickStart() {
    set({ quickStartOpen: false });
  },
  async dismissQuickStart() {
    try {
      const updated = await endpoints.settings.update({ ui: { quick_start_version: 1 } });
      set((state) => ({
        quickStartOpen: false,
        health: state.health
          ? { ...state.health, ui: { ...state.health.ui, ...updated.ui } }
          : state.health,
      }));
    } catch (error) {
      get().reportError(
        error,
        get().locale === "zh-CN" ? "保存快速开始偏好" : "saving the quick start preference",
      );
    }
  },
  openProjectCreator() {
    set({ view: "projects", projectCreatorOpen: true, quickStartOpen: false, paletteOpen: false });
  },
  closeProjectCreator() {
    set({ projectCreatorOpen: false });
  },
  togglePanel(open) {
    set((state) => ({ panelOpen: open ?? !state.panelOpen }));
  },
  toggleAssistant(open) {
    set((state) => ({ assistantOpen: open ?? !state.assistantOpen }));
  },
  setSidebarWidth(width) {
    set({ sidebarWidth: Math.max(220, Math.min(620, width)) });
  },
  async setLocale(locale) {
    const previous = get().locale;
    set({ locale });
    try {
      await endpoints.settings.update({ ui: { locale } });
    } catch (error) {
      set({ locale: previous });
      get().reportError(
        error,
        locale === "zh-CN" ? "保存界面语言" : "saving interface language",
      );
      throw error;
    }
  },

  notify(toast) {
    const id = `t${++toastCounter}`;
    set((state) => ({ toasts: [...state.toasts, { ...toast, id }] }));
    // Errors stay until dismissed. Routine saves acknowledge quickly so they do
    // not cover lower-right controls for five seconds.
    if (toast.kind !== "error") {
      const timeout = toast.kind === "success" ? 1600 : toast.kind === "info" ? 2400 : 3800;
      setTimeout(() => get().dismissToast(id), timeout);
    }
  },
  dismissToast(id) {
    set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) }));
  },

  reportError(error, context) {
    if (error instanceof ApiError) {
      // Actionable failures get a button that takes the user to the fix, rather
      // than a message they have to interpret.
      const action = error.isConfiguration
        ? ({ label: get().locale === "zh-CN" ? "打开设置" : "Open Settings", view: "settings" as ViewId })
        : error.isMissingDependency
          ? ({ label: get().locale === "zh-CN" ? "查看依赖要求" : "See requirements", view: "settings" as ViewId })
          : undefined;
      get().notify({
        kind: "error",
        message: context ? `${context}: ${error.message}` : error.message,
        detail: error.hint || undefined,
        action,
      });
      return;
    }
    get().notify({
      kind: "error",
      message: context
        ? get().locale === "zh-CN" ? `${context}失败` : `${context} failed`
        : get().locale === "zh-CN" ? "发生了错误" : "Something went wrong",
      detail: String(error),
    });
  },

  // -------------------------------------------------------------- projects
  async loadProjects() {
    try {
      const { items, importable } = await endpoints.projects.list();
      set({ projects: items, importable });
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? "加载项目" : "loading projects");
    }
  },

  async openProject(projectId) {
    try {
      const bundle = await endpoints.projects.get(projectId);
      await endpoints.workbench.updateState(projectId);
      set({
        activeProjectId: projectId,
        project: bundle.project,
        document: bundle.document,
        stats: bundle.stats,
        git: bundle.git,
        analyses: bundle.analyses,
        dirtySections: {},
        activeSectionKey: bundle.document.sections[0]?.key ?? "",
        searchResult: null,
        analysis: null,
        view: "editor",
      });
      await Promise.allSettled([
        get().loadLibrary({ project_id: projectId }),
        get().loadRuns(),
        get().loadTimeline(),
      ]);
      if (bundle.latest_analysis_id) {
        await get().openAnalysis(bundle.latest_analysis_id);
      }
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? "打开项目" : "opening the project");
    }
  },

  closeProject() {
    void endpoints.workbench.updateState("");
    set({
      activeProjectId: "", project: null, document: null, stats: null, git: null,
      analysis: null, analyses: [], runs: [], activeRun: null, timeline: [],
      dirtySections: {}, activeSectionKey: "", view: "projects",
    });
  },

  async reloadDocument() {
    const projectId = get().activeProjectId;
    if (!projectId) return;
    try {
      const { document, stats } = await endpoints.writing.document(projectId);
      set((state) => {
        const keys = new Set(document.sections.map((section) => section.key));
        const activeSectionKey = keys.has(state.activeSectionKey)
          ? state.activeSectionKey
          : document.sections[0]?.key ?? "";
        const dirtySections = Object.fromEntries(
          Object.entries(state.dirtySections).filter(([key]) => keys.has(key)),
        );
        return { document, stats, activeSectionKey, dirtySections };
      });
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? "重新加载手稿" : "reloading the manuscript");
    }
  },

  setActiveSection(key) {
    set({ activeSectionKey: key, view: "editor" });
  },

  editSection(key, content) {
    set((state) => ({ dirtySections: { ...state.dirtySections, [key]: content } }));
  },

  async saveSection(key) {
    const { activeProjectId, dirtySections } = get();
    const content = dirtySections[key];
    if (!activeProjectId || content === undefined) return;
    try {
      const { section } = await endpoints.writing.updateSection(activeProjectId, key, { content });
      set((state) => {
        const next = { ...state.dirtySections };
        // A slow save must not erase text typed while the request was in flight.
        if (next[key] === content) delete next[key];
        // Update the persisted section in the same state transition as clearing
        // dirty text. Otherwise CodeMirror briefly receives the old server value
        // after focus moves to the Save button and reports it as a fresh edit.
        const document = state.document
          ? {
              ...state.document,
              sections: state.document.sections.map((entry) =>
                entry.key === key ? section : entry,
              ),
            }
          : null;
        return { document, dirtySections: next };
      });
      await get().reloadDocument();
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? `保存章节 ${key}` : `saving ${key}`);
    }
  },

  async saveAllSections() {
    const keys = Object.keys(get().dirtySections);
    for (const key of keys) {
      await get().saveSection(key);
    }
    if (keys.length) {
      get().notify({ kind: "success", message: get().locale === "zh-CN" ? `已保存 ${keys.length} 个章节` : `Saved ${keys.length} section(s)` });
    }
  },

  // ---------------------------------------------------------------- search
  async loadProviders() {
    try {
      const { providers } = await endpoints.search.providers();
      set({ providers });
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? "加载检索源" : "loading search providers");
    }
  },

  async runSearch(body) {
    set({ searchRunning: true, searchProgress: [], searchResult: null });
    try {
      const { job_id } = await endpoints.search.submit(body);
      const result = (await events.waitForJob(job_id)) as unknown as SearchResponse;
      set({ searchResult: result, searchRunning: false });
      const found = result?.papers?.length ?? 0;
      const failed = (result?.stats ?? []).filter(
        (stat) => stat.outcome !== "success" || Boolean(stat.error),
      );
      get().notify({
        kind: failed.length || !found ? "warning" : "success",
        message: get().locale === "zh-CN"
          ? found
            ? failed.length
              ? `找到 ${found} 篇论文；${failed.length} 个检索源失败`
              : `找到 ${found} 篇论文（合并 ${result.duplicates_merged} 条重复记录）`
            : failed.length
              ? `未返回论文；已记录 ${failed.length} 个检索源失败`
              : "没有匹配的论文"
          : found
          ? failed.length
            ? `Found ${found} papers; ${failed.length} provider(s) failed`
            : `Found ${found} papers (${result.duplicates_merged} duplicates merged)`
          : failed.length
            ? `No papers returned; ${failed.length} provider failure(s) saved`
            : "No papers matched",
        detail: result?.warnings?.join("; ") || undefined,
      });
      const projectId = get().activeProjectId;
      if (projectId) await get().loadLibrary({ project_id: projectId });
    } catch (error) {
      set({ searchRunning: false });
      get().reportError(error, get().locale === "zh-CN" ? "文献检索" : "search");
    }
  },

  async loadLibrary(query) {
    try {
      const { items, total } = await endpoints.library.browse({ limit: 200, ...query });
      set({ libraryPapers: items, libraryTotal: total });
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? "加载文献库" : "loading the library");
    }
  },

  togglePaperSelected(paperId) {
    set((state) => ({
      selectedPaperIds: state.selectedPaperIds.includes(paperId)
        ? state.selectedPaperIds.filter((id) => id !== paperId)
        : [...state.selectedPaperIds, paperId],
    }));
  },
  clearSelection() {
    set({ selectedPaperIds: [] });
  },

  // -------------------------------------------------------------- analysis
  async loadAnalyses() {
    const projectId = get().activeProjectId;
    try {
      const { items } = await endpoints.analysis.list(projectId);
      set({ analyses: items });
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? "加载分析" : "loading analyses");
    }
  },

  async openAnalysis(analysisId) {
    set({ analysisLoading: true });
    try {
      const detail = await endpoints.analysis.get(analysisId, {
        include_points: true,
        include_heatmap: true,
        // Keyword layers are megabytes; fetched individually on demand.
        include_layers: false,
        include_papers: true,
      });
      set({
        analysis: detail, analysisLoading: false, activeLayer: "",
        highlightedClusters: [], selectedGapId: "",
      });
    } catch (error) {
      set({ analysisLoading: false });
      get().reportError(error, get().locale === "zh-CN" ? "打开分析" : "opening the analysis");
    }
  },

  async buildAnalysis(overrides) {
    const projectId = get().activeProjectId;
    if (!projectId) {
      get().notify({ kind: "warning", message: get().locale === "zh-CN" ? "请先打开一个项目" : "Open a project first" });
      return;
    }
    set({ analysisLoading: true });
    try {
      const { job_id } = await endpoints.analysis.submit({
        project_id: projectId,
        ...overrides,
      });
      const result = await events.waitForJob(job_id);
      const analysisId = String(result.analysis_id || "");
      await get().loadAnalyses();
      if (analysisId) await get().openAnalysis(analysisId);
      const warnings = (result.warnings as string[]) || [];
      get().notify({
        kind: warnings.length ? "warning" : "success",
        message: get().locale === "zh-CN" ? `图谱已生成：${result.n_clusters} 个聚类，${result.n_gaps} 个空白候选` : `Landscape built: ${result.n_clusters} clusters, ${result.n_gaps} gap candidates`,
        detail: warnings.join("; ") || undefined,
      });
      set({ view: "landscape" });
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? "生成论文图谱" : "building the landscape");
    } finally {
      set({ analysisLoading: false });
    }
  },

  async setActiveLayer(term) {
    const analysis = get().analysis;
    if (!analysis) return;
    if (!term) {
      set({ activeLayer: "" });
      return;
    }
    if (analysis.heatmap?.layers?.[term]) {
      set({ activeLayer: term });
      return;
    }
    try {
      const layer = await endpoints.analysis.layer(analysis.analysis_id, term);
      set((state) => ({
        activeLayer: term,
        analysis: state.analysis
          ? {
              ...state.analysis,
              heatmap: state.analysis.heatmap
                ? {
                    ...state.analysis.heatmap,
                    layers: { ...state.analysis.heatmap.layers, [term]: layer.grid },
                  }
                : state.analysis.heatmap,
            }
          : state.analysis,
      }));
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? `加载“${term}”热力层` : `loading the '${term}' layer`);
    }
  },

  toggleClusterHighlight(clusterId) {
    set((state) => ({
      highlightedClusters: state.highlightedClusters.includes(clusterId)
        ? state.highlightedClusters.filter((id) => id !== clusterId)
        : [...state.highlightedClusters, clusterId],
    }));
  },
  selectGap(gapId) {
    set((state) => ({ selectedGapId: state.selectedGapId === gapId ? "" : gapId }));
  },

  // ---------------------------------------------------------------- agents
  async loadRuns() {
    const projectId = get().activeProjectId;
    if (!projectId) return;
    try {
      const { items } = await endpoints.agents.runs(projectId);
      set({ runs: items });
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? "加载 Agent 运行记录" : "loading agent runs");
    }
  },

  async startAgentRun(body) {
    const projectId = get().activeProjectId;
    if (!projectId) {
      get().notify({ kind: "warning", message: get().locale === "zh-CN" ? "请先打开一个项目" : "Open a project first" });
      return;
    }
    try {
      const { run_id, job_id } = await endpoints.agents.run({
        project_id: projectId,
        skill_ids: get().enabledSkillIds,
        ...body,
      });
      set({ streaming: {}, view: "agents" });
      await get().loadRuns();
      await get().openRun(run_id);
      get().notify({ kind: "info", message: get().locale === "zh-CN" ? "Agent 已开始运行" : "Agent run started" });
      void events
        .waitForJob(job_id)
        .then(async () => {
          await Promise.allSettled([
            get().openRun(run_id),
            get().loadRuns(),
            get().reloadDocument(),
            get().loadTimeline(),
          ]);
          get().notify({ kind: "success", message: get().locale === "zh-CN" ? "Agent 运行已完成" : "Agent run finished" });
        })
        .catch(async (error) => {
          // A failed worker persists the run, failed step and both recovery
          // snapshots before JOB_FAILED is emitted. Refresh all durable state
          // first so the user never sees a stale "running" run after failure.
          await Promise.allSettled([
            get().openRun(run_id),
            get().loadRuns(),
            get().reloadDocument(),
            get().loadTimeline(),
          ]);
          if (error instanceof events.JobFailureError) {
            const failure = error.failure;
            const outcome = String(failure.outcome || "unexpected_error");
            const hint = String(failure.hint || "Open the run audit for recovery details.");
            get().notify({
              kind: "error",
              message: get().locale === "zh-CN" ? `Agent 运行失败：${outcome.replaceAll("_", " ")}` : `Agent run failed: ${outcome.replaceAll("_", " ")}`,
              detail: hint,
              action:
                outcome === "configuration_error" || outcome === "authentication_error"
                  ? { label: get().locale === "zh-CN" ? "打开模型设置" : "Open model settings", view: "settings" }
                  : undefined,
            });
          } else {
            get().reportError(error, get().locale === "zh-CN" ? "Agent 运行" : "agent run");
          }
        });
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? "启动 Agent 运行" : "starting the agent run");
    }
  },

  async openRun(runId) {
    try {
      set({ activeRun: await endpoints.agents.getRun(runId) });
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? "打开运行记录" : "opening the run");
    }
  },

  async cancelRun(runId) {
    try {
      await endpoints.agents.cancel(runId);
      get().notify({
        kind: "info",
        message: get().locale === "zh-CN" ? "已请求取消" : "Cancellation requested",
        detail: get().locale === "zh-CN" ? "运行将在下一步停止；已经写入的内容会保留。" : "The run stops at its next step; work already written is kept.",
      });
      await get().loadRuns();
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? "取消 Agent 运行" : "cancelling the run");
    }
  },

  // ---------------------------------------------------------------- skills
  async loadSkills() {
    try {
      const { items } = await endpoints.skills.list(get().activeProjectId);
      set({
        skills: items,
        // Enabled skills are pre-selected for runs; the user can still change it.
        enabledSkillIds: items.filter((s) => s.enabled).map((s) => s.id),
      });
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? "加载技能" : "loading skills");
    }
  },

  toggleSkill(skillId) {
    set((state) => ({
      enabledSkillIds: state.enabledSkillIds.includes(skillId)
        ? state.enabledSkillIds.filter((id) => id !== skillId)
        : [...state.enabledSkillIds, skillId],
    }));
  },

  async loadTimeline() {
    const projectId = get().activeProjectId;
    if (!projectId) return;
    try {
      // The timeline endpoint deliberately returns only a compact Git summary.
      // VersionsView also needs the complete staged/unstaged/untracked lists for
      // safe discard controls, so never let that summary masquerade as status.
      const [{ entries }, git] = await Promise.all([
        endpoints.versions.timeline(projectId),
        endpoints.versions.gitStatus(projectId),
      ]);
      set({ timeline: entries, git });
    } catch (error) {
      get().reportError(error, get().locale === "zh-CN" ? "加载版本历史" : "loading version history");
    }
  },

  async loadJobs() {
    try {
      const { items } = await endpoints.system.jobs({ limit: 30 });
      set({ jobs: items });
    } catch {
      /* the jobs panel is informational; a failure here is not worth a toast */
    }
  },

  appendBackendLog(line) {
    set((state) => ({
      backendLog: [...state.backendLog.slice(-1500), { ts: Date.now(), ...line }],
    }));
  },
}));

/** Wire SSE events into store updates. Called once, during boot. */
function subscribeToEvents(
  set: (partial: Partial<StoreState> | ((s: StoreState) => Partial<StoreState>)) => void,
  get: () => StoreState,
): void {
  events.on<{
    provider: string;
    providerName: string;
    count: number;
    outcome: ProviderStats["outcome"];
    error: string;
    retryable: boolean;
    hint: string;
  }>(
    "search.provider",
    (event) => {
      set((state) => ({
        searchProgress: [
          ...state.searchProgress,
          {
            provider: event.payload.providerName || event.payload.provider,
            count: event.payload.count,
            outcome: event.payload.outcome,
            error: event.payload.error,
            retryable: event.payload.retryable,
            hint: event.payload.hint,
          },
        ],
      }));
    },
  );

  // Streaming section text: appended per section so the editor can show it live.
  events.on<{ sectionKey: string; delta: string }>("agent.step.delta", (event) => {
    const key = event.payload.sectionKey || "_";
    set((state) => ({
      streaming: {
        ...state.streaming,
        [key]: (state.streaming[key] ?? "") + event.payload.delta,
      },
    }));
  });

  events.on("agent.step.done", () => {
    const run = get().activeRun;
    if (run) void get().openRun(run.id);
  });

  events.on("document.updated", () => {
    void get().reloadDocument();
  });

  events.on("library.updated", () => {
    const projectId = get().activeProjectId;
    void get().loadLibrary(projectId ? { project_id: projectId } : undefined);
  });

  events.on<{ kind: string; progress: number; message: string }>("job.progress", () => {
    void get().loadJobs();
  });

  events.on<{ kind: string }>("job.created", () => {
    void get().loadJobs();
  });

  events.on<{ kind: string; result: Record<string, unknown> }>("job.done", (event) => {
    void get().loadJobs();
    if (event.payload.kind === "resource_import") {
      void Promise.all([
        get().loadWorkbench(),
        get().loadWorkbenchResources(),
        get().loadLibrary(
          get().activeProjectId ? { project_id: get().activeProjectId } : undefined,
        ),
        get().activeProjectId ? get().reloadDocument() : Promise.resolve(),
      ]);
    }
  });

  events.on<{ kind: string; error: string }>("job.failed", () => {
    void get().loadJobs();
  });

  events.on<{ message: string; kind?: Toast["kind"] }>("notify", (event) => {
    get().notify({
      kind: event.payload.kind ?? "info",
      message: String(event.payload.message ?? ""),
    });
  });
}

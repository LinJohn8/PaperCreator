/**
 * Search view: keyword, idea-based and paper-based retrieval.
 *
 * The mode selector is the important control. Keyword search is what people
 * expect; idea and paper modes are the ones that answer the actual requirement
 * ("find work related to my idea"), and they behave differently enough - query
 * expansion, semantic providers - that showing what will be sent matters.
 */

import { useEffect, useRef, useState } from "react";

import * as endpoints from "../api/endpoints";
import { useStore } from "../state/store";
import type { Paper, ProviderInfo, ProviderStats } from "../api/types";

type Mode = "keyword" | "idea" | "paper";

interface SearchHistoryEntry {
  id: string;
  query: string;
  mode: Mode;
  seed_text: string;
  providers: string[];
  params: Record<string, unknown>;
  provider_stats: Record<string, Partial<ProviderStats>>;
  result_count: number;
  created_at: string;
}

export function SearchView() {
  const providers = useStore((s) => s.providers);
  const searchResult = useStore((s) => s.searchResult);
  const searchRunning = useStore((s) => s.searchRunning);
  const searchProgress = useStore((s) => s.searchProgress);
  const runSearch = useStore((s) => s.runSearch);
  const loadProviders = useStore((s) => s.loadProviders);
  const libraryPapers = useStore((s) => s.libraryPapers);
  const loadLibrary = useStore((s) => s.loadLibrary);
  const project = useStore((s) => s.project);
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);

  const [mode, setMode] = useState<Mode>("keyword");
  const [query, setQuery] = useState("");
  const [seedText, setSeedText] = useState("");
  const [selectedProviders, setSelectedProviders] = useState<string[]>([]);
  const [yearFrom, setYearFrom] = useState<number | "">("");
  const [yearTo, setYearTo] = useState<number | "">("");
  const [openAccessOnly, setOpenAccessOnly] = useState(false);
  const [perProvider, setPerProvider] = useState(40);
  const [useLlmExpansion, setUseLlmExpansion] = useState(true);
  const [expansion, setExpansion] = useState<Record<string, any> | null>(null);
  const [expanding, setExpanding] = useState(false);
  const [seedPaperId, setSeedPaperId] = useState("");
  const providerDefaultsInitialised = useRef(false);
  const [history, setHistory] = useState<SearchHistoryEntry[]>([]);

  // Availability is dynamic: importing a .bib file can make the local provider
  // usable after application boot, and settings can enable/disable other sources.
  useEffect(() => {
    void loadProviders();
  }, [loadProviders]);

  useEffect(() => {
    if (mode !== "paper") return;
    void loadLibrary(project ? { project_id: project.id } : undefined);
  }, [loadLibrary, mode, project]);

  useEffect(() => {
    void refreshHistory();
  }, [project?.id]);

  async function refreshHistory() {
    try {
      const result = await endpoints.search.history(project?.id ?? "", 20);
      setHistory(result.items as SearchHistoryEntry[]);
    } catch (error) {
      useStore.getState().reportError(error, "loading search history");
    }
  }

  // Default the provider selection to what is enabled and working.
  useEffect(() => {
    if (providerDefaultsInitialised.current || !providers.length) return;
    providerDefaultsInitialised.current = true;
    setSelectedProviders(
      providers.filter((p) => p.enabled && p.available).map((p) => p.id),
    );
  }, [providers]);

  // Seed idea mode from the project's own idea - that is usually what the user
  // wants to search for, and retyping it is pure friction.
  useEffect(() => {
    if (mode !== "keyword" && !seedText && project?.idea) setSeedText(project.idea);
  }, [mode, project?.idea, seedText]);

  const semanticProviders = providers.filter((p) => p.capabilities.semantic_query);

  async function preview() {
    setExpanding(true);
    try {
      setExpansion(
        await endpoints.search.expand({
          query,
          seed_text: mode === "keyword" ? "" : seedText,
          use_llm: useLlmExpansion,
        }),
      );
    } catch (error) {
      useStore.getState().reportError(error, locale === "zh-CN" ? "扩展检索式" : "expanding the query");
    } finally {
      setExpanding(false);
    }
  }

  async function submit() {
    if (mode === "keyword" && !query.trim()) {
      notify({ kind: "warning", message: locale === "zh-CN" ? "请输入检索式" : "Enter a query" });
      return;
    }
    if (mode !== "keyword" && !seedText.trim()) {
      notify({
        kind: "warning",
        message: locale === "zh-CN" ? "请填写想法或摘要" : "Describe the idea or paste an abstract",
      });
      return;
    }
    if (!selectedProviders.length) {
      notify({ kind: "warning", message: locale === "zh-CN" ? "请至少选择一个检索源" : "Select at least one source" });
      return;
    }
    await runSearch({
      query,
      mode,
      seed_text: mode === "keyword" ? "" : seedText,
      providers: selectedProviders,
      limit_per_provider: perProvider,
      total_limit: Math.max(50, perProvider * selectedProviders.length),
      year_from: yearFrom || null,
      year_to: yearTo || null,
      open_access_only: openAccessOnly,
      project_id: project?.id ?? "",
      use_llm_expansion: useLlmExpansion,
    });
    await refreshHistory();
  }

  async function rerun(entry: SearchHistoryEntry) {
    await runSearch({
      ...entry.params,
      project_id: project?.id ?? "",
    });
    await refreshHistory();
  }

  async function retryFailedProviders(resultStats: ProviderStats[], request: Record<string, unknown>) {
    const retryable = resultStats
      .filter((stat) => stat.outcome !== "success" && stat.retryable)
      .map((stat) => stat.provider);
    if (!retryable.length) return;
    await runSearch({
      ...request,
      providers: retryable,
      use_cache: false,
      project_id: project?.id ?? "",
    });
    await refreshHistory();
  }

  return (
    <div className="view">
      <h1>{locale === "zh-CN" ? "文献检索" : "Literature search"}</h1>
      <p className="sub">
        {locale === "zh-CN"
          ? "并行查询多个免费学术数据库，跨源去重合并，再用倒数排名融合排序。"
          : "Queries several free scholarly databases in parallel, merges duplicates across them, and ranks by reciprocal rank fusion."}
      </p>

      <div className="card">
        <div className="row wrap" style={{ marginBottom: 12 }}>
          {(["keyword", "idea", "paper"] as Mode[]).map((entry) => (
            <button
              key={entry}
              className={`btn${mode === entry ? " primary" : ""}`}
              onClick={() => setMode(entry)}
            >
              {entry === "keyword"
                ? locale === "zh-CN" ? "关键词" : "Keywords"
                : entry === "idea"
                  ? locale === "zh-CN" ? "按我的想法" : "From my idea"
                  : locale === "zh-CN" ? "按已有论文" : "From a paper"}
            </button>
          ))}
        </div>

        {mode === "keyword" ? (
          <div className="field">
            <label>{locale === "zh-CN" ? "检索词" : "Query"}</label>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void submit();
              }}
              placeholder="graph neural network molecular property prediction"
            />
          </div>
        ) : (
          <>
            {mode === "paper" && (
              <div className="field">
                <label>{locale === "zh-CN" ? "从文献库选择已有论文" : "Choose an existing paper"}</label>
                <select
                  aria-label={locale === "zh-CN" ? "已有论文" : "Existing paper"}
                  value={seedPaperId}
                  onChange={(event) => {
                    const paperId = event.target.value;
                    setSeedPaperId(paperId);
                    const paper = libraryPapers.find((entry) => entry.id === paperId);
                    if (paper) {
                      setSeedText([paper.title, paper.abstract].filter(Boolean).join("\n\n"));
                    }
                  }}
                >
                  <option value="">
                    {locale === "zh-CN" ? "手工粘贴标题或摘要…" : "Paste a title or abstract manually…"}
                  </option>
                  {libraryPapers.map((paper) => (
                    <option key={paper.id} value={paper.id}>
                      {paper.title}
                    </option>
                  ))}
                </select>
                <span className="hint">
                  {locale === "zh-CN"
                    ? "选择后会把论文标题和摘要作为检索种子；下方仍可编辑。"
                    : "The title and abstract become the search seed and remain editable below."}
                </span>
              </div>
            )}
            <div className="field">
              <label>
                {mode === "idea"
                  ? locale === "zh-CN" ? "你的想法" : "Your idea"
                  : locale === "zh-CN" ? "论文摘要" : "Paper abstract"}
              </label>
              <textarea
                rows={5}
                value={seedText}
                onChange={(event) => setSeedText(event.target.value)}
                placeholder={
                  mode === "idea"
                    ? locale === "zh-CN"
                      ? "描述你想研究的问题和方法…"
                      : "Describe the problem and approach you have in mind…"
                    : locale === "zh-CN"
                      ? "粘贴一篇论文的摘要，检索与之相关的工作…"
                      : "Paste an abstract to find work related to it…"
                }
              />
              <span className="hint">
                {locale === "zh-CN"
                  ? "这段文字会被扩展成多个检索式：关键词库只接受短语查询，直接投一整段摘要会检索不到任何结果。"
                  : "This text is expanded into several queries. Keyword databases only match phrases — sending a whole abstract to them returns nothing."}
              </span>
            </div>
            <div className="row wrap">
              <label className="row" style={{ gap: 6 }}>
                <input
                  type="checkbox"
                  checked={useLlmExpansion}
                  onChange={(event) => setUseLlmExpansion(event.target.checked)}
                />
                {locale === "zh-CN" ? "用 LLM 扩展检索式" : "Expand queries with the LLM"}
              </label>
              <button className="btn sm" onClick={() => void preview()} disabled={expanding}>
                {expanding
                  ? locale === "zh-CN" ? "生成中…" : "Expanding…"
                  : locale === "zh-CN" ? "预览检索式" : "Preview queries"}
              </button>
              {semanticProviders.length > 0 && (
                <span className="dim" style={{ fontSize: "var(--fs-xs)" }}>
                  {locale === "zh-CN" ? "语义检索源：" : "semantic sources: "}
                  {semanticProviders.map((p) => p.name).join(", ")}
                </span>
              )}
            </div>
            {expansion && (
              <div className="card" style={{ marginTop: 10 }}>
                <h3>
                  {locale === "zh-CN" ? "将要执行的检索式" : "Queries that will be sent"}{" "}
                  <span className="dim">({String(expansion.method)})</span>
                </h3>
                <div className="row wrap" style={{ gap: 5 }}>
                  {(expansion.queries as string[]).map((entry) => (
                    <span key={entry} className="chip on">
                      {entry}
                    </span>
                  ))}
                </div>
                {Array.isArray(expansion.synonyms) && expansion.synonyms.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <span className="dim">
                      {locale === "zh-CN" ? "同义词：" : "synonyms: "}
                    </span>
                    {(expansion.synonyms as string[]).join(", ")}
                  </div>
                )}
                {expansion.notes ? (
                  <p className="warn-text" style={{ fontSize: "var(--fs-sm)" }}>
                    {String(expansion.notes)}
                  </p>
                ) : null}
              </div>
            )}
          </>
        )}

        <div className="row wrap" style={{ marginTop: 4 }}>
          <div className="field" style={{ width: 110, marginBottom: 0 }}>
            <label>{locale === "zh-CN" ? "起始年" : "From year"}</label>
            <input
              type="number"
              value={yearFrom}
              onChange={(event) => setYearFrom(Number(event.target.value) || "")}
              placeholder="2018"
            />
          </div>
          <div className="field" style={{ width: 110, marginBottom: 0 }}>
            <label>{locale === "zh-CN" ? "结束年" : "To year"}</label>
            <input
              type="number"
              value={yearTo}
              onChange={(event) => setYearTo(Number(event.target.value) || "")}
            />
          </div>
          <div className="field" style={{ width: 150, marginBottom: 0 }}>
            <label>{locale === "zh-CN" ? "每源上限" : "Per source"}</label>
            <input
              type="number"
              value={perProvider}
              min={5}
              max={500}
              onChange={(event) => setPerProvider(Number(event.target.value) || 40)}
            />
          </div>
          <label className="row" style={{ gap: 6, marginTop: 18 }}>
            <input
              type="checkbox"
              checked={openAccessOnly}
              onChange={(event) => setOpenAccessOnly(event.target.checked)}
            />
            {locale === "zh-CN" ? "仅开放获取" : "Open access only"}
          </label>
        </div>

        <h3 style={{ marginTop: 16 }}>
          {locale === "zh-CN" ? "检索源" : "Sources"}{" "}
          <span className="dim">
            ({selectedProviders.length}/{providers.length})
          </span>
        </h3>
        <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))" }}>
          {providers.map((provider) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              selected={selectedProviders.includes(provider.id)}
              onToggle={() =>
                setSelectedProviders((current) =>
                  current.includes(provider.id)
                    ? current.filter((id) => id !== provider.id)
                    : [...current, provider.id],
                )
              }
            />
          ))}
        </div>

        <div className="row" style={{ marginTop: 16 }}>
          <button className="btn primary" onClick={() => void submit()} disabled={searchRunning}>
            {searchRunning
              ? locale === "zh-CN" ? "检索中…" : "Searching…"
              : locale === "zh-CN" ? "开始检索" : "Search"}
          </button>
          {!project && (
            <span className="dim">
              {locale === "zh-CN"
                ? "未打开项目：结果会进入全局文献库。"
                : "No project open — results go into the global library."}
            </span>
          )}
        </div>
      </div>

      {searchRunning && searchProgress.length > 0 && (
        <div className="card">
          <h3>{locale === "zh-CN" ? "各源进度" : "Provider progress"}</h3>
          {searchProgress.map((entry, index) => (
            <div key={`${entry.provider}-${index}`} className="row" style={{ padding: "2px 0" }}>
              <span className={`dot ${entry.error ? "closed" : "open"}`} />
              <span className="grow">{entry.provider}</span>
              {entry.error ? (
                <span className="err-text" title={entry.hint}>
                  {providerOutcomeLabel(entry.outcome, locale)} · {entry.error}
                  {entry.retryable ? (locale === "zh-CN" ? " · 可重试" : " · retryable") : ""}
                </span>
              ) : (
                <span className="dim">{entry.count} results</span>
              )}
            </div>
          ))}
        </div>
      )}

      {searchResult && (
        <SearchResults
          onRetry={(stats, request) => void retryFailedProviders(stats, request)}
        />
      )}

      {history.length > 0 && (
        <div className="card">
          <h2>{locale === "zh-CN" ? "检索历史" : "Search history"}</h2>
          <p className="hint">
            {locale === "zh-CN"
              ? "保存实际执行参数和结果数量；重新检索会建立一条新记录，不覆盖原记录。"
              : "Stores the executed parameters and result count. Running again creates a new record rather than overwriting the original."}
          </p>
          <table
            className="data"
            aria-label={locale === "zh-CN" ? "检索历史" : "Search history"}
          >
            <thead>
              <tr>
                <th style={{ width: 80 }}>{locale === "zh-CN" ? "模式" : "Mode"}</th>
                <th>{locale === "zh-CN" ? "检索种子" : "Seed"}</th>
                <th style={{ width: 80 }}>{locale === "zh-CN" ? "结果" : "Results"}</th>
                <th style={{ width: 145 }}>{locale === "zh-CN" ? "时间" : "When"}</th>
                <th style={{ width: 100 }} />
              </tr>
            </thead>
            <tbody>
              {history.map((entry) => (
                <tr key={entry.id}>
                  <td><span className="chip">{entry.mode}</span></td>
                  <td>
                    <div>{entry.query || entry.seed_text.slice(0, 120) || "—"}</div>
                    <div className="dim" style={{ fontSize: "var(--fs-xs)" }}>
                      {entry.providers.join(", ") || "—"}
                      {entry.params.use_llm_expansion === false
                        ? locale === "zh-CN" ? " · 规则扩展" : " · rule expansion"
                        : ""}
                    </div>
                    {Object.values(entry.provider_stats ?? {}).some((stat) => stat.error) && (
                      <div className="err-text" style={{ fontSize: "var(--fs-xs)" }}>
                        {Object.values(entry.provider_stats ?? {}).filter((stat) => stat.error).length}{" "}
                        {locale === "zh-CN" ? "个来源失败（诊断已保存）" : "source failure(s) saved"}
                      </div>
                    )}
                  </td>
                  <td className="num">{entry.result_count}</td>
                  <td className="dim">{entry.created_at.slice(0, 16).replace("T", " ")}</td>
                  <td>
                    <button
                      className="btn sm"
                      disabled={searchRunning}
                      onClick={() => void rerun(entry)}
                    >
                      {locale === "zh-CN" ? "重新检索" : "Run again"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ProviderCard({
  provider,
  selected,
  onToggle,
}: {
  provider: ProviderInfo;
  selected: boolean;
  onToggle: () => void;
}) {
  const locale = useStore((s) => s.locale);
  const setView = useStore((s) => s.setView);

  return (
    <div
      className="card"
      aria-label={`search-provider-${provider.id}`}
      style={{
        marginBottom: 0,
        borderColor: selected ? "var(--focus)" : undefined,
        opacity: provider.available ? 1 : 0.6,
      }}
    >
      <label className="row" style={{ gap: 8, alignItems: "flex-start" }}>
        <input
          type="checkbox"
          checked={selected}
          disabled={!provider.available}
          onChange={onToggle}
          style={{ marginTop: 3 }}
        />
        <span className="grow">
          <span style={{ fontWeight: 600 }}>
            {locale === "zh-CN" && provider.name_zh ? provider.name_zh : provider.name}
          </span>
          <div className="dim" style={{ fontSize: "var(--fs-xs)" }}>
            {provider.coverage}
          </div>
        </span>
        <span className={`chip ${provider.tier === "free" ? "ok" : "warn"}`}>
          {locale === "zh-CN" && provider.tier === "free" ? "免费" : provider.tier}
        </span>
      </label>

      <p className="muted" style={{ fontSize: "var(--fs-xs)", margin: "6px 0" }}>
        {locale === "zh-CN" && provider.description_zh
          ? provider.description_zh
          : provider.description}
      </p>

      <div className="row wrap" style={{ gap: 4 }}>
        {provider.capabilities.semantic_query && (
          <span className="chip ok" title={locale === "zh-CN" ? "支持从想法进行真实语义检索" : "Supports true semantic search from an idea"}>
            {locale === "zh-CN" ? "语义检索" : "semantic"}
          </span>
        )}
        {provider.capabilities.returns_citations && <span className="chip">{locale === "zh-CN" ? "引用数" : "citations"}</span>}
        {provider.capabilities.returns_references && <span className="chip">{locale === "zh-CN" ? "参考关系" : "references"}</span>}
        {!provider.capabilities.returns_abstract && (
          <span className="chip warn" title={locale === "zh-CN" ? "很少返回摘要，主要用于元数据" : "Rarely returns abstracts; useful for metadata"}>
            {locale === "zh-CN" ? "摘要稀少" : "sparse abstracts"}
          </span>
        )}
      </div>

      {provider.unavailable_reason && (
        <div style={{ marginTop: 8 }}>
          <div className="warn-text" style={{ fontSize: "var(--fs-xs)" }}>
            {provider.unavailable_reason}
          </div>
          {provider.requires_key ? (
            <button className="btn sm" style={{ marginTop: 4 }} onClick={() => setView("settings")}>
              {locale === "zh-CN" ? "配置 API key" : "Configure key"}
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}

function SearchResults({
  onRetry,
}: {
  onRetry: (stats: ProviderStats[], request: Record<string, unknown>) => void;
}) {
  const result = useStore((s) => s.searchResult)!;
  const locale = useStore((s) => s.locale);
  const project = useStore((s) => s.project);
  const [showAll, setShowAll] = useState(false);

  const shown = showAll ? result.papers : result.papers.slice(0, 40);
  const failed = result.stats.filter(
    (stat) => stat.outcome !== "success" || Boolean(stat.error),
  );
  const retryable = failed.filter((stat) => stat.retryable);

  return (
    <>
      <h2>
        {locale === "zh-CN" ? "检索结果" : "Results"}{" "}
        <span className="dim">
          {result.papers.length} {locale === "zh-CN" ? "篇" : "papers"}
        </span>
      </h2>

      <div className="row wrap" style={{ marginBottom: 10 }}>
        <span className="chip">
          {locale === "zh-CN" ? "去重前" : "before dedupe"} {result.total_before_dedupe}
        </span>
        <span className="chip">
          {locale === "zh-CN" ? "合并" : "merged"} {result.duplicates_merged}
        </span>
        {result.stats.map((stat) => (
          <span
            key={stat.provider}
            className={`chip ${stat.outcome !== "success" || stat.error ? "err" : "ok"}`}
            title={stat.error || `${stat.duration_ms}ms`}
          >
            {stat.provider} {stat.outcome !== "success" || stat.error ? "✕" : stat.count}
          </span>
        ))}
      </div>

      {result.warnings.map((warning, index) => (
        <p key={index} className="warn-text" style={{ fontSize: "var(--fs-sm)" }}>
          ⚠ {warning}
        </p>
      ))}

      {failed.length > 0 && (
        <div
          className="card"
          aria-label={locale === "zh-CN" ? "检索源诊断" : "Provider diagnostics"}
          style={{ borderColor: "var(--warn)", marginTop: 10 }}
        >
          <div className="row wrap">
            <h3 className="grow">
              {result.papers.length
                ? locale === "zh-CN" ? "部分检索源失败" : "Some providers failed"
                : locale === "zh-CN" ? "检索源未返回结果" : "Providers returned no results"}
            </h3>
            <span className="chip warn">
              {failed.length}/{result.stats.length} {locale === "zh-CN" ? "失败" : "failed"}
            </span>
          </div>
          {failed.map((stat) => (
            <div
              key={stat.provider}
              className="row wrap"
              style={{ padding: "8px 0", borderTop: "1px solid var(--border)" }}
            >
              <strong style={{ minWidth: 120 }}>{stat.provider}</strong>
              <span className="chip err">{providerOutcomeLabel(stat.outcome, locale)}</span>
              {stat.http_status ? <span className="chip">HTTP {stat.http_status}</span> : null}
              {stat.retry_after_s ? (
                <span className="chip">retry-after {stat.retry_after_s}s</span>
              ) : null}
              <span className="err-text grow">{stat.error}</span>
              {stat.retryable && (
                <span className="chip warn">{locale === "zh-CN" ? "可重试" : "retryable"}</span>
              )}
              {stat.hint && (
                <div className="hint" style={{ flexBasis: "100%" }}>
                  {providerHint(stat, locale)}
                </div>
              )}
            </div>
          ))}
          {retryable.length > 0 && (
            <button
              className="btn"
              onClick={() => onRetry(retryable, result.request ?? {})}
              disabled={useStore.getState().searchRunning}
              style={{ marginTop: 8 }}
            >
              {locale === "zh-CN"
                ? `仅重试 ${retryable.length} 个可恢复来源`
                : `Retry ${retryable.length} recoverable source(s)`}
            </button>
          )}
        </div>
      )}

      <table
        className="data"
        aria-label={locale === "zh-CN" ? "检索结果" : "Search results"}
      >
        <thead>
          <tr>
            <th style={{ width: 52 }}>{locale === "zh-CN" ? "得分" : "Score"}</th>
            <th>{locale === "zh-CN" ? "标题" : "Title"}</th>
            <th style={{ width: 56 }}>{locale === "zh-CN" ? "年份" : "Year"}</th>
            <th style={{ width: 62 }} className="num">
              {locale === "zh-CN" ? "引用" : "Cites"}
            </th>
            <th style={{ width: 150 }}>{locale === "zh-CN" ? "来源" : "Sources"}</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((paper) => (
            <ResultRow key={paper.id} paper={paper} />
          ))}
        </tbody>
      </table>

      {result.papers.length > shown.length && (
        <button className="btn" style={{ marginTop: 10 }} onClick={() => setShowAll(true)}>
          {locale === "zh-CN"
            ? `显示全部 ${result.papers.length} 篇`
            : `Show all ${result.papers.length}`}
        </button>
      )}

      {project && (
        <p className="dim" style={{ marginTop: 12, fontSize: "var(--fs-sm)" }}>
          {locale === "zh-CN"
            ? `已加入项目「${project.title}」的文献集合，可以直接生成研究图谱。`
            : `Added to "${project.title}" — you can build the landscape now.`}
        </p>
      )}
    </>
  );
}

function providerOutcomeLabel(outcome: ProviderStats["outcome"], locale: string): string {
  const labels: Record<ProviderStats["outcome"], [string, string]> = {
    success: ["成功", "success"],
    unavailable: ["不可用", "unavailable"],
    rate_limited: ["触发限流", "rate limited"],
    timeout: ["超时", "timeout"],
    authentication_error: ["认证失败", "authentication"],
    http_error: ["HTTP 错误", "HTTP error"],
    network_error: ["网络错误", "network error"],
    invalid_response: ["响应格式异常", "invalid response"],
    provider_error: ["来源错误", "provider error"],
    unexpected_error: ["解析器异常", "unexpected error"],
  };
  return labels[outcome ?? "provider_error"][locale === "zh-CN" ? 0 : 1];
}

function providerHint(stat: ProviderStats, locale: string): string {
  if (locale !== "zh-CN") return stat.hint;
  const hints: Partial<Record<ProviderStats["outcome"], string>> = {
    unavailable: "在设置中配置该来源，或取消选择后改用其他来源。",
    rate_limited: "等待配额恢复后，仅重试这个来源。",
    timeout: "检查网络、代理和防火墙，然后重试；其他来源的结果仍然有效。",
    authentication_error: "检查设置中的 API key 和账号权限。",
    http_error: "服务端错误可稍后重试；持续的 4xx 通常需要调整查询或配置。",
    network_error: "检查 DNS、代理和防火墙，然后重试。",
    invalid_response: "先重试一次；持续失败可能表示来源接口格式已经变化。",
    provider_error: "可重试该来源，或取消选择后继续使用其他来源。",
    unexpected_error: "来源解析器出现异常；重试前应先查看日志。",
  };
  return hints[stat.outcome] ?? stat.hint;
}

function ResultRow({ paper }: { paper: Paper }) {
  const [open, setOpen] = useState(false);
  const ranking = (paper.raw?.ranking ?? {}) as Record<string, number>;

  return (
    <>
      <tr onClick={() => setOpen(!open)} style={{ cursor: "pointer" }}>
        <td className="num mono" title={JSON.stringify(ranking)}>
          {paper.score.toFixed(3)}
        </td>
        <td>
          <div>{paper.title}</div>
          <div className="dim" style={{ fontSize: "var(--fs-xs)" }}>
            {paper.authors.slice(0, 3).map((author) => author.name).join(", ")}
            {paper.authors.length > 3 ? " et al." : ""}
            {paper.venue ? ` · ${paper.venue}` : ""}
            {paper.is_open_access ? " · OA" : ""}
          </div>
        </td>
        <td>{paper.year ?? "—"}</td>
        <td className="num">{paper.citation_count}</td>
        <td className="dim" style={{ fontSize: "var(--fs-xs)" }}>
          {paper.source_providers.join(", ")}
        </td>
      </tr>
      {open && (
        <tr>
          <td />
          <td colSpan={4} style={{ paddingBottom: 12 }}>
            {paper.abstract ? (
              <p style={{ userSelect: "text", margin: "4px 0" }}>{paper.abstract}</p>
            ) : (
              <p className="dim">(no abstract available from these sources)</p>
            )}
            <div className="row wrap" style={{ gap: 5 }}>
              {paper.doi && <span className="chip mono">doi:{paper.doi}</span>}
              {paper.arxiv_id && <span className="chip mono">arXiv:{paper.arxiv_id}</span>}
              {Object.entries(ranking).map(([key, value]) => (
                <span key={key} className="chip" title="ranking component">
                  {key} {Number(value).toFixed(2)}
                </span>
              ))}
              {paper.url && (
                <button
                  className="btn sm"
                  onClick={() => void window.papercreator?.shell.openExternal(paper.url)}
                >
                  open
                </button>
              )}
            </div>
            {Array.isArray((paper.raw as any)?.conflicts?.year) && (
              <p className="warn-text" style={{ fontSize: "var(--fs-xs)" }}>
                ⚠ sources disagree on the year:{" "}
                {((paper.raw as any).conflicts.year as number[]).join(" vs ")} — the earlier one is
                used
              </p>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

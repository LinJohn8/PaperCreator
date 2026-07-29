/**
 * Status bar: the numbers worth watching continuously.
 *
 * Word count against target, citation coverage, running jobs, embedding backend
 * quality, and LLM configuration. The embedding item is here rather than buried
 * in settings because it silently determines analysis quality - a user should not
 * discover after an hour that their landscape was built from lexical vectors.
 */

import { useEffect } from "react";

import { useStore } from "../state/store";

export function StatusBar() {
  const stats = useStore((s) => s.stats);
  const project = useStore((s) => s.project);
  const health = useStore((s) => s.health);
  const jobs = useStore((s) => s.jobs);
  const analysis = useStore((s) => s.analysis);
  const setView = useStore((s) => s.setView);
  const loadJobs = useStore((s) => s.loadJobs);
  const togglePanel = useStore((s) => s.togglePanel);
  const dirtyCount = useStore((s) => Object.keys(s.dirtySections).length);
  const locale = useStore((s) => s.locale);

  useEffect(() => {
    void loadJobs();
    const timer = setInterval(() => void loadJobs(), 8000);
    return () => clearInterval(timer);
  }, [loadJobs]);

  const running = jobs.filter((job) => job.status === "running" || job.status === "queued");
  const activeBackend = health?.analysis.embedding_backends.find((b) => b.available);
  const llmReady = health?.llm.has_any ?? false;
  const providerCount = health?.retrieval.available.length ?? 0;

  // The embedding backend actually used by the open landscape, if there is one,
  // otherwise the one that would be chosen now.
  const usedBackend = analysis?.embedding_model?.split(":")[0] ?? "";
  const embeddingQuality =
    usedBackend === "st" || activeBackend?.id === "sentence-transformers"
      ? "semantic"
      : usedBackend === "llm"
        ? "semantic"
        : "lexical";

  return (
    <footer className="statusbar">
      {project && stats && (
        <>
          <button className="item" onClick={() => setView("editor")} title="Manuscript progress">
            ✎ {stats.words.toLocaleString()}
            {stats.target_words > 0 && ` / ${stats.target_words.toLocaleString()}`}{" "}
            {locale === "zh-CN" ? "词" : "words"}
            {stats.target_words > 0 && ` (${Math.round(stats.completion * 100)}%)`}
          </button>
          {stats.words_zh > 0 && (
            <span className="item" title="Paired-language word count">
              中 {stats.words_zh.toLocaleString()}
            </span>
          )}
          <button
            className="item"
            onClick={() => setView("library")}
            title={`${stats.papers_cited} of ${stats.papers_in_project} project papers are cited`}
          >
            ❑ {stats.papers_cited}/{stats.papers_in_project}{" "}
            {locale === "zh-CN" ? "已引用" : "cited"}
          </button>
        </>
      )}

      {dirtyCount > 0 && (
        <button
          className="item"
          onClick={() => void useStore.getState().saveAllSections()}
          title="Ctrl+S"
        >
          ● {dirtyCount} {locale === "zh-CN" ? "未保存" : "unsaved"}
        </button>
      )}

      <div className="spacer" />

      {running.length > 0 && (
        <button className="item" onClick={() => togglePanel(true)}>
          <span className="spin">◌</span>
          {running.length === 1
            ? `${running[0].kind} ${Math.round((running[0].progress || 0) * 100)}%`
            : `${running.length} ${locale === "zh-CN" ? "个任务" : "jobs"}`}
        </button>
      )}

      {analysis && (
        <button className="item" onClick={() => setView("landscape")}>
          ◈ {analysis.n_papers} {locale === "zh-CN" ? "篇" : "papers"} · {analysis.n_clusters}{" "}
          {locale === "zh-CN" ? "簇" : "clusters"} · {analysis.gaps.length}{" "}
          {locale === "zh-CN" ? "缺口" : "gaps"}
        </button>
      )}

      <button
        className="item"
        onClick={() => setView("settings")}
        title={
          embeddingQuality === "semantic"
            ? "Semantic embeddings: clusters reflect meaning"
            : "Lexical embeddings (TF-IDF): clusters reflect shared vocabulary only. " +
              "Install the analysis extra for semantic quality."
        }
      >
        {embeddingQuality === "semantic" ? "◉" : "◎"}{" "}
        {embeddingQuality === "semantic"
          ? locale === "zh-CN" ? "语义向量" : "semantic"
          : locale === "zh-CN" ? "词频向量" : "lexical"}
      </button>

      <button
        className="item"
        onClick={() => setView("settings")}
        title={
          llmReady
            ? `LLM providers: ${health?.llm.usable.join(", ")}`
            : "No LLM configured — agent features are unavailable"
        }
      >
        {llmReady ? "◆" : "◇"} {llmReady ? health?.llm.usable.length : 0} LLM
      </button>

      <button
        className="item"
        onClick={() => setView("search")}
        title={`${providerCount} retrieval providers available`}
      >
        ⌕ {providerCount}
      </button>

      <button className="item" onClick={() => togglePanel()} title="Ctrl+`">
        {locale === "zh-CN" ? "输出" : "Output"}
      </button>
    </footer>
  );
}

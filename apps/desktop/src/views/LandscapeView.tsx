/**
 * Landscape view: the 3D map plus the controls and inspector around it.
 *
 * This is where the "see the gaps, and see where my idea sits" requirement is
 * actually delivered, so the panel is built around interpretation rather than
 * decoration: every gap shows its evidence and its caveat, and placing an idea
 * reports what its position means in words, not just coordinates.
 */

import { useEffect, useMemo, useState } from "react";

import * as endpoints from "../api/endpoints";
import { CLUSTER_COLOURS, Landscape3D, type HoverInfo } from "../components/Landscape3D";
import { useStore } from "../state/store";
import type { AnalysisPaperRow, GapCandidate, PositionResult } from "../api/types";

export function LandscapeView() {
  const analysis = useStore((s) => s.analysis);
  const analyses = useStore((s) => s.analyses);
  const loading = useStore((s) => s.analysisLoading);
  const buildAnalysis = useStore((s) => s.buildAnalysis);
  const openAnalysis = useStore((s) => s.openAnalysis);
  const highlighted = useStore((s) => s.highlightedClusters);
  const selectedGapId = useStore((s) => s.selectedGapId);
  const selectGap = useStore((s) => s.selectGap);
  const activeLayer = useStore((s) => s.activeLayer);
  const setActiveLayer = useStore((s) => s.setActiveLayer);
  const paperCount = useStore((s) => s.stats?.papers_in_project ?? 0);
  const health = useStore((s) => s.health);
  const locale = useStore((s) => s.locale);

  const [rows, setRows] = useState<AnalysisPaperRow[]>([]);
  const [hover, setHover] = useState<HoverInfo | null>(null);
  const [selected, setSelected] = useState<AnalysisPaperRow | null>(null);
  const [showDensity, setShowDensity] = useState(true);
  const [showGaps, setShowGaps] = useState(true);
  const [colourMode, setColourMode] =
    useState<"cluster" | "year" | "citations" | "density">("cluster");
  const [placement, setPlacement] = useState<PositionResult | null>(null);

  useEffect(() => {
    if (!analysis) {
      setRows([]);
      return;
    }
    let cancelled = false;
    void endpoints.analysis
      .papers(analysis.analysis_id)
      .then((result) => {
        if (!cancelled) setRows(result.items);
      })
      .catch((error) => useStore.getState().reportError(error, "loading landscape points"));
    return () => {
      cancelled = true;
    };
  }, [analysis]);

  const selectedGap = useMemo(
    () => analysis?.gaps.find((gap) => gap.id === selectedGapId) ?? null,
    [analysis, selectedGapId],
  );

  const layerNames = useMemo(() => {
    if (!analysis?.heatmap) return [];
    const fromLayers = Object.keys(analysis.heatmap.layers ?? {});
    return fromLayers.length ? fromLayers : (analysis.heatmap.layer_names ?? []);
  }, [analysis]);

  if (!analysis) {
    return (
      <div className="view">
        <h1>{locale === "zh-CN" ? "研究图谱" : "Research landscape"}</h1>
        <p className="sub">
          {locale === "zh-CN"
            ? "把检索到的论文投影到三维空间，按主题聚类，并标出可能的研究缺口。"
            : "Projects the retrieved papers into 3D, clusters them by topic, and marks candidate research gaps."}
        </p>

        {analyses.length > 0 && (
          <div className="card">
            <h3>{locale === "zh-CN" ? "已有图谱" : "Existing landscapes"}</h3>
            {analyses.map((entry) => (
              <div key={entry.analysis_id} className="row" style={{ padding: "3px 0" }}>
                <button className="btn sm" onClick={() => void openAnalysis(entry.analysis_id)}>
                  {locale === "zh-CN" ? "打开" : "Open"}
                </button>
                <span className="grow truncate">{entry.name}</span>
                <span className="dim">
                  {entry.n_papers} papers · {entry.n_clusters} clusters · {entry.reducer}
                </span>
              </div>
            ))}
          </div>
        )}

        <div className="card">
          <h3>{locale === "zh-CN" ? "生成新图谱" : "Build a landscape"}</h3>
          <p className="muted">
            {paperCount > 0
              ? locale === "zh-CN"
                ? `将使用项目中的 ${paperCount} 篇论文。`
                : `Uses the ${paperCount} papers in this project.`
              : locale === "zh-CN"
                ? "项目中还没有论文，请先执行检索。"
                : "This project has no papers yet — run a search first."}
          </p>
          {health && (
            <p className="dim" style={{ fontSize: "var(--fs-sm)" }}>
              {locale === "zh-CN" ? "当前算法：" : "Current stack: "}
              {health.analysis.embedding_backends.find((b) => b.available)?.name ?? "?"} ·{" "}
              {health.analysis.reducers.find((r) => r.available)?.name ?? "?"} ·{" "}
              {health.analysis.clusterers.find((c) => c.available)?.name ?? "?"}
            </p>
          )}
          <div className="row">
            <button
              className="btn primary"
              disabled={loading || paperCount === 0}
              onClick={() => void buildAnalysis()}
            >
              {loading
                ? locale === "zh-CN" ? "生成中…" : "Building…"
                : locale === "zh-CN" ? "生成图谱" : "Build landscape"}
            </button>
            <button
              className="btn"
              disabled={loading || paperCount === 0}
              onClick={() =>
                void buildAnalysis({
                  embedding_backend: "hashing",
                  reducer: "pca",
                  clusterer: "kmeans",
                })
              }
              title="Deterministic lexical map that can place new ideas offline without moving existing points"
            >
              {locale === "zh-CN"
                ? "可增量（Hashing + PCA）"
                : "Incremental (Hashing + PCA)"}
            </button>
          </div>
        </div>

        <GapDetectorHelp />
      </div>
    );
  }

  return (
    <div className="landscape">
      <div className="canvas-wrap">
        {rows.length > 0 && (
          <Landscape3D
            analysis={analysis}
            rows={rows}
            highlightedClusters={highlighted}
            selectedGapId={selectedGapId}
            activeLayer={activeLayer}
            showDensity={showDensity}
            showGaps={showGaps}
            colourMode={colourMode}
            onSelect={setSelected}
            onHover={setHover}
          />
        )}

        <div className="overlay tl">
          <div className="row wrap" style={{ gap: 6 }}>
            <select
              value={colourMode}
              onChange={(event) => setColourMode(event.target.value as typeof colourMode)}
              title="What the point colours encode"
            >
              <option value="cluster">{locale === "zh-CN" ? "按主题簇着色" : "colour: cluster"}</option>
              <option value="year">{locale === "zh-CN" ? "按年份着色" : "colour: year"}</option>
              <option value="citations">{locale === "zh-CN" ? "按引用数着色" : "colour: citations"}</option>
              <option value="density">{locale === "zh-CN" ? "按密度着色" : "colour: density"}</option>
            </select>
            <label className="chip clickable">
              <input
                type="checkbox"
                checked={showDensity}
                onChange={(event) => setShowDensity(event.target.checked)}
              />
              {locale === "zh-CN" ? "密度面" : "density"}
            </label>
            <label className="chip clickable">
              <input
                type="checkbox"
                checked={showGaps}
                onChange={(event) => setShowGaps(event.target.checked)}
              />
              {locale === "zh-CN" ? "缺口" : "gaps"} ({analysis.gaps.length})
            </label>
          </div>
          {layerNames.length > 0 && (
            <div className="row" style={{ marginTop: 6 }}>
              <select
                value={activeLayer}
                onChange={(event) => void setActiveLayer(event.target.value)}
                title="Show where papers containing a term are concentrated"
                style={{ maxWidth: 240 }}
              >
                <option value="">
                  {locale === "zh-CN" ? "关键词热力层：无" : "keyword layer: none"}
                </option>
                {layerNames.map((term) => (
                  <option key={term} value={term}>
                    {term}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="dim" style={{ marginTop: 6, fontSize: "var(--fs-xs)" }}>
            {locale === "zh-CN"
              ? "左键拖动旋转 · 右键/Shift 平移 · 滚轮缩放"
              : "drag to rotate · right-drag or shift to pan · wheel to zoom"}
          </div>
        </div>

        <div className="overlay tr">
          <div style={{ fontWeight: 600, marginBottom: 4 }}>
            {locale === "zh-CN" ? "图例" : "Clusters"}
          </div>
          {analysis.clusters.slice(0, 12).map((cluster) => (
            <div
              key={cluster.id}
              className={`legend-item${highlighted.length && !highlighted.includes(cluster.id) ? " dim" : ""}`}
              onClick={() => useStore.getState().toggleClusterHighlight(cluster.id)}
              title={cluster.keywords.slice(0, 8).join(", ")}
            >
              <span
                className="legend-swatch"
                style={{ background: CLUSTER_COLOURS[cluster.id % CLUSTER_COLOURS.length] }}
              />
              <span className="truncate" style={{ maxWidth: 180 }}>
                {locale === "zh-CN" && cluster.label_zh ? cluster.label_zh : cluster.label}
              </span>
              <span className="dim">{cluster.size}</span>
            </div>
          ))}
          {rows.some((row) => row.is_seed) && (
            <div className="legend-item">
              <span className="legend-swatch" style={{ background: "var(--c-seed)" }} />
              {locale === "zh-CN" ? "我的想法 / 论文" : "my idea / paper"}
            </div>
          )}
          {rows.some((row) => row.cluster < 0) && (
            <div className="legend-item">
              <span className="legend-swatch" style={{ background: "var(--c-noise)" }} />
              {locale === "zh-CN" ? "离群（未归簇）" : "outliers (unclustered)"}
            </div>
          )}
        </div>

        <div className="overlay bl">
          <span className="dim">
            {analysis.n_papers} {locale === "zh-CN" ? "篇" : "papers"} ·{" "}
            {analysis.reducer.toUpperCase()} / {analysis.clusterer} ·{" "}
            {analysis.embedding_model}
          </span>
          {typeof analysis.metrics.trustworthiness === "number" && (
            <span
              className="dim"
              title="How well the 3D layout preserves the original neighbourhoods (0-1). Below ~0.7, treat visual proximity with caution."
            >
              {" "}
              · trust {Number(analysis.metrics.trustworthiness).toFixed(2)}
            </span>
          )}
        </div>

        {hover && (
          <div
            className="overlay hovercard"
            style={{
              left: Math.min(hover.x + 14, window.innerWidth - 420),
              top: hover.y + 14,
            }}
          >
            <div style={{ fontWeight: 600 }}>{hover.row.title}</div>
            <div className="dim">
              {hover.row.authors.join(", ")}
              {hover.row.authors.length >= 3 ? " et al." : ""} · {hover.row.year ?? "n.d."}
            </div>
            <div className="dim">
              {hover.row.venue} · {hover.row.citations} citations
            </div>
            <div className="dim">
              {hover.row.cluster >= 0
                ? `cluster ${hover.row.cluster}: ${hover.row.cluster_label}`
                : "outlier"}
              {hover.row.is_seed ? " · my idea" : ""}
            </div>
          </div>
        )}
      </div>

      <aside className="inspector">
        {selectedGap ? (
          <GapDetail gap={selectedGap} onClose={() => selectGap(selectedGap.id)} />
        ) : selected ? (
          <PaperDetail row={selected} onClose={() => setSelected(null)} />
        ) : placement ? (
          <PlacementDetail result={placement} onClose={() => setPlacement(null)} />
        ) : (
          <InspectorDefault
            onPlaced={(result) => {
              setPlacement(result);
              void useStore.getState().openAnalysis(analysis.analysis_id);
            }}
          />
        )}
      </aside>
    </div>
  );
}

function InspectorDefault({ onPlaced }: { onPlaced: (result: PositionResult) => void }) {
  const analysis = useStore((s) => s.analysis)!;
  const projectId = useStore((s) => s.activeProjectId);
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const [title, setTitle] = useState("");
  const [abstract, setAbstract] = useState("");
  const [busy, setBusy] = useState(false);
  const [graph, setGraph] = useState<Record<string, any> | null>(null);

  async function place() {
    if (!title.trim() && !abstract.trim()) {
      notify({ kind: "warning", message: locale === "zh-CN" ? "请先描述想法" : "Describe the idea first" });
      return;
    }
    setBusy(true);
    try {
      const result = await endpoints.analysis.placeIdea(analysis.analysis_id, {
        title: title.trim() || abstract.trim().slice(0, 100),
        abstract: abstract.trim(),
        project_id: projectId,
      });
      onPlaced(result);
      setTitle("");
      setAbstract("");
    } catch (error) {
      reportError(error, "placing the idea");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h3 style={{ marginTop: 0 }}>
        {locale === "zh-CN" ? "把我的想法放进图谱" : "Place my idea on the map"}
      </h3>
      <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
        {locale === "zh-CN"
          ? "已有的点不会移动：系统复用同一个降维模型，只投影新的点。"
          : "Existing points do not move — the same fitted projection is reused for the new point only."}
      </p>
      <div className="field">
        <label>{locale === "zh-CN" ? "标题" : "Title"}</label>
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder={
            locale === "zh-CN" ? "一句话概括你的想法" : "One line describing the idea"
          }
        />
      </div>
      <div className="field">
        <label>{locale === "zh-CN" ? "描述 / 摘要" : "Description / abstract"}</label>
        <textarea
          rows={6}
          value={abstract}
          onChange={(event) => setAbstract(event.target.value)}
          placeholder={
            locale === "zh-CN"
              ? "越具体，定位越准确"
              : "The more specific, the more accurate the placement"
          }
        />
      </div>
      <button className="btn primary" onClick={() => void place()} disabled={busy}>
        {busy
          ? locale === "zh-CN" ? "定位中…" : "Placing…"
          : locale === "zh-CN" ? "定位" : "Place it"}
      </button>

      <h3 style={{ marginTop: 22 }}>{locale === "zh-CN" ? "图谱指标" : "Metrics"}</h3>
      <dl className="kv">
        <dt>{locale === "zh-CN" ? "文献" : "papers"}</dt>
        <dd>{analysis.n_papers}</dd>
        <dt>{locale === "zh-CN" ? "聚类" : "clusters"}</dt>
        <dd>{analysis.n_clusters}</dd>
        <dt>{locale === "zh-CN" ? "嵌入模型" : "embedding"}</dt>
        <dd className="mono">{analysis.embedding_model}</dd>
        <dt>{locale === "zh-CN" ? "降维算法" : "reducer"}</dt>
        <dd>{analysis.reducer}</dd>
        <dt>{locale === "zh-CN" ? "聚类算法" : "clusterer"}</dt>
        <dd>{analysis.clusterer}</dd>
        {typeof analysis.metrics.cluster_silhouette === "number" && (
          <>
            <dt title="Higher is better; below 0.1 the clusters overlap heavily.">
              silhouette
            </dt>
            <dd>{Number(analysis.metrics.cluster_silhouette).toFixed(3)}</dd>
          </>
        )}
        {typeof analysis.metrics.trustworthiness === "number" && (
          <>
            <dt title="How faithfully the 3D layout preserves neighbourhoods.">
              trustworthiness
            </dt>
            <dd>{Number(analysis.metrics.trustworthiness).toFixed(3)}</dd>
          </>
        )}
        {typeof analysis.metrics.cluster_n_noise === "number" && (
          <>
            <dt>{locale === "zh-CN" ? "离群点" : "outliers"}</dt>
            <dd>{String(analysis.metrics.cluster_n_noise)}</dd>
          </>
        )}
      </dl>

      {analysis.warnings.length > 0 && (
        <>
          <h3 style={{ marginTop: 18 }}>{locale === "zh-CN" ? "提示" : "Notes"}</h3>
          {analysis.warnings.map((warning, index) => (
            <p key={index} className="warn-text" style={{ fontSize: "var(--fs-sm)" }}>
              {warning}
            </p>
          ))}
        </>
      )}

      <h3 style={{ marginTop: 18 }}>{locale === "zh-CN" ? "引用网络" : "Citation graph"}</h3>
      {graph ? (
        <dl className="kv">
          <dt>{locale === "zh-CN" ? "数据覆盖率" : "coverage"}</dt>
          <dd title="Share of papers with reference data — only OpenAlex supplies it.">
            {Math.round((graph.citation?.coverage ?? 0) * 100)}%
          </dd>
          <dt>{locale === "zh-CN" ? "内部引用边" : "internal links"}</dt>
          <dd>{graph.citation?.internal_edges ?? 0}</dd>
          <dt>{locale === "zh-CN" ? "孤立文献" : "isolated"}</dt>
          <dd>{graph.citation?.isolated_papers ?? 0}</dd>
          <dt>{locale === "zh-CN" ? "图内最高被引" : "most cited here"}</dt>
          <dd className="truncate">
            {graph.influential_papers?.[0]?.title ?? "—"}
          </dd>
        </dl>
      ) : (
        <button
          className="btn sm"
          onClick={() =>
            void endpoints.analysis
              .graph(analysis.analysis_id)
              .then(setGraph)
              .catch((error) => reportError(error, "loading the citation graph"))
          }
        >
          {locale === "zh-CN" ? "分析引用网络" : "Analyse citation graph"}
        </button>
      )}
    </>
  );
}

function GapDetail({ gap, onClose }: { gap: GapCandidate; onClose: () => void }) {
  const locale = useStore((s) => s.locale);
  const startAgentRun = useStore((s) => s.startAgentRun);

  return (
    <>
      <div className="row">
        <h3 className="grow" style={{ marginTop: 0 }}>
          {gap.kind.replace(/_/g, " ")}
        </h3>
        <button className="btn icon sm" onClick={onClose} aria-label={locale === "zh-CN" ? "关闭缺口详情" : "Close gap details"}>
          ✕
        </button>
      </div>
      <div className="row" style={{ marginBottom: 10 }}>
        <span className={`chip ${gap.score >= 0.6 ? "warn" : ""}`}>
          score {gap.score.toFixed(2)}
        </span>
        {gap.keywords.slice(0, 4).map((keyword) => (
          <span key={keyword} className="chip">
            {keyword}
          </span>
        ))}
      </div>
      <p>{locale === "zh-CN" && gap.description_zh ? gap.description_zh : gap.description}</p>

      <h3>{locale === "zh-CN" ? "证据" : "Evidence"}</h3>
      <dl className="kv">
        {Object.entries(gap.evidence)
          .filter(([key]) => key !== "caveat")
          .map(([key, value]) => (
            <div key={key} style={{ display: "contents" }}>
              <dt>{key.replace(/_/g, " ")}</dt>
              <dd className="mono">
                {typeof value === "object" ? JSON.stringify(value) : String(value)}
              </dd>
            </div>
          ))}
      </dl>
      {typeof gap.evidence.caveat === "string" && (
        <p className="warn-text" style={{ fontSize: "var(--fs-sm)" }}>
          ⚠ {gap.evidence.caveat}
        </p>
      )}

      <h3>{locale === "zh-CN" ? "下一步" : "What to do with this"}</h3>
      <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
        {locale === "zh-CN"
          ? "这是基于检索到的元数据的启发式判断，不能证明该工作不存在。建议先用这些关键词做一次定向检索验证。"
          : "This is a heuristic over the retrieved metadata, not proof that the work does not exist. Verify it with a targeted search on these keywords first."}
      </p>
      <div className="row wrap">
        <button
          className="btn sm"
          onClick={() => {
            useStore.getState().setView("search");
            useStore.getState().notify({
              kind: "info",
              message: locale === "zh-CN" ? "请检索这些缺口关键词进行验证" : "Search these gap keywords to verify it",
              detail: gap.keywords.join(", "),
            });
          }}
        >
          {locale === "zh-CN" ? "定向检索验证" : "Verify with a search"}
        </button>
        <button
          className="btn sm"
          onClick={() => void startAgentRun({ pipeline: "custom", custom_roles: ["ideator"] })}
          title={locale === "zh-CN" ? "让缺口分析智能体判断哪些候选可能真实存在" : "Ask the gap-analysis agent to judge which of these candidates are real"}
        >
          {locale === "zh-CN" ? "让 AI 评估缺口" : "Ask the AI to validate"}
        </button>
      </div>
    </>
  );
}

function PaperDetail({ row, onClose }: { row: AnalysisPaperRow; onClose: () => void }) {
  const locale = useStore((s) => s.locale);
  const [paper, setPaper] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    if (row.missing) return;
    void endpoints.library.get(row.paper_id).then(setPaper).catch(() => setPaper(null));
  }, [row.paper_id, row.missing]);

  return (
    <>
      <div className="row">
        <h3 className="grow" style={{ marginTop: 0 }}>
          {row.title}
        </h3>
        <button className="btn icon sm" onClick={onClose} aria-label={locale === "zh-CN" ? "关闭论文详情" : "Close paper details"}>
          ✕
        </button>
      </div>
      <dl className="kv">
        <dt>{locale === "zh-CN" ? "作者" : "Authors"}</dt>
        <dd>{row.authors.join(", ") || "—"}</dd>
        <dt>{locale === "zh-CN" ? "年份" : "Year"}</dt>
        <dd>{row.year ?? "—"}</dd>
        <dt>{locale === "zh-CN" ? "来源" : "Venue"}</dt>
        <dd>{row.venue || "—"}</dd>
        <dt>{locale === "zh-CN" ? "引用" : "Citations"}</dt>
        <dd>{row.citations}</dd>
        <dt>{locale === "zh-CN" ? "所属簇" : "Cluster"}</dt>
        <dd>{row.cluster >= 0 ? `${row.cluster} · ${row.cluster_label}` : "outlier"}</dd>
        <dt>{locale === "zh-CN" ? "坐标" : "Position"}</dt>
        <dd className="mono">
          {row.x.toFixed(2)}, {row.y.toFixed(2)}, {row.z.toFixed(2)}
        </dd>
        <dt title="Local neighbourhood density, 0-1">
          {locale === "zh-CN" ? "局部密度" : "Density"}
        </dt>
        <dd>{row.density.toFixed(3)}</dd>
      </dl>
      {paper?.abstract && (
        <>
          <h3>{locale === "zh-CN" ? "摘要" : "Abstract"}</h3>
          <p style={{ fontSize: "var(--fs-sm)", userSelect: "text" }}>{paper.abstract}</p>
        </>
      )}
      {paper?.url && (
        <button
          className="btn sm"
          onClick={() => void window.papercreator?.shell.openExternal(paper.url)}
        >
          {locale === "zh-CN" ? "打开原文" : "Open source"}
        </button>
      )}
    </>
  );
}

function PlacementDetail({
  result,
  onClose,
}: {
  result: PositionResult;
  onClose: () => void;
}) {
  const locale = useStore((s) => s.locale);
  const analysisId = result.analysis_id;

  return (
    <>
      <div className="row">
        <h3 className="grow" style={{ marginTop: 0 }}>
          {locale === "zh-CN" ? "定位结果" : "Where it landed"}
        </h3>
        <button className="btn icon sm" onClick={onClose} aria-label={locale === "zh-CN" ? "关闭定位结果" : "Close placement result"}>
          ✕
        </button>
      </div>
      <div className="row wrap" style={{ marginBottom: 10 }}>
        <span className="chip on">
          {locale === "zh-CN" ? "新颖度" : "novelty"} {result.novelty.toFixed(2)}
        </span>
        <span className="chip">
          {locale === "zh-CN" ? "密度分位" : "density pct"}{" "}
          {Math.round(result.density_percentile * 100)}%
        </span>
        <span
          className="chip"
          title={
            result.method === "exact_transform"
              ? "The stored reducer transformed the new vector directly"
              : "The point was interpolated from its nearest existing neighbours"
          }
        >
          {result.method === "exact_transform"
            ? locale === "zh-CN" ? "精确投影" : "exact projection"
            : locale === "zh-CN" ? "邻居插值" : "neighbour interpolation"}
        </span>
        {result.nearest_cluster >= 0 && (
          <span className="chip">
            {locale === "zh-CN" ? "最近簇" : "cluster"} {result.nearest_cluster_label}
          </span>
        )}
      </div>
      <p>{locale === "zh-CN" ? result.interpretation_zh : result.interpretation}</p>

      <h3>{locale === "zh-CN" ? "最相近的工作" : "Closest existing work"}</h3>
      {result.nearest_papers.map((near) => (
        <div key={near.paper_id} className="row" style={{ padding: "2px 0" }}>
          <span className="mono" style={{ minWidth: 46 }}>
            {near.similarity.toFixed(3)}
          </span>
          <span className="grow truncate" title={near.title}>
            {near.title}
          </span>
        </div>
      ))}

      {result.nearest_gaps.length > 0 && (
        <>
          <h3>{locale === "zh-CN" ? "附近的缺口" : "Nearby gaps"}</h3>
          {result.nearest_gaps.map((gap) => (
            <div key={gap.id} className="row" style={{ padding: "2px 0" }}>
              <span className={`chip ${gap.inside ? "warn" : ""}`}>
                {gap.inside ? (locale === "zh-CN" ? "落在其中" : "inside") : gap.distance.toFixed(1)}
              </span>
              <span className="grow truncate">{gap.kind.replace(/_/g, " ")}</span>
            </div>
          ))}
        </>
      )}

      <button
        className="btn sm danger"
        style={{ marginTop: 14 }}
        onClick={() =>
          void endpoints.analysis
            .removePoints(analysisId, [result.paper_id])
            .then(() => {
              useStore.getState().notify({
                kind: "info",
                message: locale === "zh-CN" ? "已从本图谱移除" : "Removed from this landscape",
                detail: locale === "zh-CN" ? "论文仍保留在文献库中。" : "The paper stays in your library.",
              });
              onClose();
              void useStore.getState().openAnalysis(analysisId);
            })
            .catch((error) => useStore.getState().reportError(error, locale === "zh-CN" ? "移除图谱点" : "removing the point"))
        }
      >
        {locale === "zh-CN" ? "从图谱中移除" : "Remove from the map"}
      </button>
    </>
  );
}

function GapDetectorHelp() {
  const health = useStore((s) => s.health);
  const locale = useStore((s) => s.locale);
  const detectors = health?.analysis.gap_detectors ?? [];
  if (!detectors.length) return null;

  return (
    <div className="card">
      <h3>{locale === "zh-CN" ? "缺口检测方法" : "How gaps are detected"}</h3>
      <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
        {locale === "zh-CN"
          ? "五种互补的启发式方法，每个候选都会给出证据与局限。它们不能证明某项工作不存在。"
          : "Five complementary heuristics. Each candidate reports its evidence and its limits — none of them prove that work does not exist."}
      </p>
      <table className="data">
        <thead>
          <tr>
            <th>{locale === "zh-CN" ? "方法" : "Detector"}</th>
            <th>{locale === "zh-CN" ? "强度" : "Strength"}</th>
            <th>{locale === "zh-CN" ? "含义" : "What it means"}</th>
          </tr>
        </thead>
        <tbody>
          {detectors.map((detector) => (
            <tr key={detector.id}>
              <td>{locale === "zh-CN" ? detector.name_zh : detector.name}</td>
              <td>
                <span
                  className={`chip ${
                    detector.strength.startsWith("high")
                      ? "ok"
                      : detector.strength === "low"
                        ? "err"
                        : "warn"
                  }`}
                >
                  {detector.strength}
                </span>
              </td>
              <td className="muted">{detector.explains}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

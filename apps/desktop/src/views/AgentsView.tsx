/**
 * Agents view: start runs, watch steps, audit prompts.
 *
 * The audit trail is the point. An agent that writes a weak section is almost
 * always a prompt or context problem, so every step's exact prompt and output are
 * viewable, and the prompt can be previewed *before* spending a model call.
 */

import { useEffect, useState, type Dispatch, type SetStateAction } from "react";

import * as endpoints from "../api/endpoints";
import { useStore } from "../state/store";
import type {
  AgentEvaluationRequest,
  AgentEvaluationSummary,
  AgentHumanEvaluation,
  AgentPipeline,
  AgentQualityReport,
  AgentReviewManuscript,
  AgentReviewPacket,
  AgentRole,
  AgentRun,
  LlmFailureDiagnostic,
} from "../api/types";

function runStatusLabel(status: string, locale: "zh-CN" | "en-US"): string {
  if (locale !== "zh-CN") return status;
  return ({ pending: "等待中", running: "运行中", done: "已完成", failed: "失败", cancelled: "已取消" } as Record<string, string>)[status] ?? status;
}

function pipelineLabel(pipeline: string, locale: "zh-CN" | "en-US"): string {
  if (locale !== "zh-CN") return pipeline;
  return ({ full_auto: "一次生成全文", section: "撰写指定章节", stitch: "拼接成文", custom: "自定义流程" } as Record<string, string>)[pipeline] ?? pipeline;
}

export function AgentsView() {
  const runs = useStore((s) => s.runs);
  const activeRun = useStore((s) => s.activeRun);
  const openRun = useStore((s) => s.openRun);
  const loadRuns = useStore((s) => s.loadRuns);
  const locale = useStore((s) => s.locale);
  const hasLlm = useStore((s) => s.health?.llm.has_any ?? false);
  const setView = useStore((s) => s.setView);
  const project = useStore((s) => s.project);

  const [pipelines, setPipelines] = useState<AgentPipeline[]>([]);
  const [roles, setRoles] = useState<AgentRole[]>([]);

  useEffect(() => {
    void endpoints.agents
      .pipelines()
      .then((result) => {
        setPipelines(result.pipelines);
        setRoles(result.roles);
      })
      .catch(() => undefined);
    void loadRuns();
  }, [loadRuns]);

  return (
    <div className="view">
      <h1>{locale === "zh-CN" ? "写作智能体" : "Writing agents"}</h1>
      <p className="sub">
        {locale === "zh-CN"
          ? "十一个专职角色，可以一次生成全文，也可以只写某几节，或把分开写的部分拼接起来。"
          : "Eleven focused roles: write the whole paper at once, draft chosen sections, or stitch separately written parts together."}
      </p>

      {hasLlm ? (
        <RunLauncher pipelines={pipelines} />
      ) : (
        <div className="card">
          <h3>{locale === "zh-CN" ? "尚未配置模型" : "No model configured"}</h3>
          <p className="muted">
            {locale === "zh-CN"
              ? "当前不能启动新运行，但历史、质量报告和人工评审仍可查看。配置 API 提供方或本地 Ollama 后即可继续生成。"
              : "New runs are disabled, but history, quality reports and human reviews remain available. Configure an API provider or local Ollama to generate again."}
          </p>
          <button className="btn primary" onClick={() => setView("settings")}>
            {locale === "zh-CN" ? "打开设置" : "Open Settings"}
          </button>
        </div>
      )}

      {activeRun && <RunDetail run={activeRun} />}

      <EvaluationSummaryPanel projectId={project?.id ?? ""} runs={runs} />

      <h2>{locale === "zh-CN" ? "运行记录" : "Run history"}</h2>
      {runs.length === 0 ? (
        <p className="dim">{locale === "zh-CN" ? "还没有运行记录。" : "No runs yet."}</p>
      ) : (
        <table
          className="data"
          aria-label={locale === "zh-CN" ? "运行记录" : "Run history"}
        >
          <thead>
            <tr>
              <th>{locale === "zh-CN" ? "流程" : "Pipeline"}</th>
              <th>{locale === "zh-CN" ? "状态" : "Status"}</th>
              <th className="num">{locale === "zh-CN" ? "步骤" : "Steps"}</th>
              <th className="num">{locale === "zh-CN" ? "令牌" : "tokens"}</th>
              <th className="num">{locale === "zh-CN" ? "成本" : "cost"}</th>
              <th>{locale === "zh-CN" ? "开始时间" : "Started"}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} className={activeRun?.id === run.id ? "selected" : ""}>
                <td>{pipelineLabel(run.pipeline, locale)}</td>
                <td>
                  <span
                    className={`chip ${
                      run.status === "done"
                        ? "ok"
                        : run.status === "failed"
                          ? "err"
                          : run.status === "running"
                            ? "on"
                            : ""
                    }`}
                  >
                    {runStatusLabel(run.status, locale)}
                  </span>
                </td>
                <td className="num">{run.step_count ?? "—"}</td>
                <td className="num">
                  {(run.tokens_in + run.tokens_out).toLocaleString()}
                </td>
                <td className="num">{run.cost_usd ? `$${run.cost_usd.toFixed(4)}` : "—"}</td>
                <td className="dim">{(run.started_at ?? run.created_at).slice(0, 16)}</td>
                <td>
                  <button className="btn sm" onClick={() => void openRun(run.id)}>
                    {locale === "zh-CN" ? "查看" : "Open"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <RoleReference roles={roles} />
    </div>
  );
}

function EvaluationSummaryPanel({
  projectId,
  runs,
}: {
  projectId: string;
  runs: AgentRun[];
}) {
  const locale = useStore((s) => s.locale);
  const [summary, setSummary] = useState<AgentEvaluationSummary | null>(null);

  useEffect(() => {
    let active = true;
    void endpoints.agents
      .evaluationSummary(projectId)
      .then((result) => {
        if (active) setSummary(result);
      })
      .catch(() => {
        if (active) setSummary(null);
      });
    return () => {
      active = false;
    };
  }, [projectId, runs]);

  if (!summary) return null;
  const dimensions = Object.entries(summary.dimensions).filter(([, value]) => value.count > 0);
  const agreement = summary.agreement;
  return (
    <section
      className="card"
      aria-label={locale === "zh-CN" ? "人工质量评审摘要" : "Human quality review summary"}
      style={{ marginTop: 12 }}
    >
      <h3>{locale === "zh-CN" ? "人工质量评审摘要" : "Human quality review summary"}</h3>
      <div className="row wrap">
        <span className="chip">{summary.reviewed_runs} {locale === "zh-CN" ? "个已评运行" : "reviewed runs"}</span>
        <span className="chip">{summary.evaluation_records} {locale === "zh-CN" ? "条评审" : "review records"}</span>
        <span className="chip ok">{summary.latest_decisions.accepted} accepted</span>
        <span className="chip on">{summary.latest_decisions.revision_required} revision</span>
        <span className="chip err">{summary.latest_decisions.rejected} rejected</span>
        <span className={`chip ${summary.decision_disagreement_runs ? "on" : ""}`}>
          {summary.decision_disagreement_runs} {locale === "zh-CN" ? "个结论分歧" : "decision disagreements"}
        </span>
      </div>
      <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
        {locale === "zh-CN"
          ? "决策与维度均按每个 Run 的最新评审统计；总评审数和分歧保留只追加历史，可用于后续模型/流水线金集比较。"
          : "Decisions and dimensions use each run's latest review; total records and disagreement preserve append-only history for later model/pipeline gold-set comparisons."}
      </p>
      {agreement && (
        <div className="card" style={{ margin: "8px 0", background: "var(--bg-side)" }}>
          <div className="row wrap">
            <strong>{locale === "zh-CN" ? "独立复评一致性" : "Independent review agreement"}</strong>
            <span className="chip">{agreement.reviewer_count} {locale === "zh-CN" ? "位评审人" : "reviewers"}</span>
            <span className="chip">{agreement.review_pair_count} {locale === "zh-CN" ? "对独立复评" : "independent pairs"}</span>
            {agreement.decision_exact_agreement != null && (
              <span className="chip">
                {locale === "zh-CN" ? "结论一致" : "Decision agreement"} {Math.round(agreement.decision_exact_agreement * 100)}%
              </span>
            )}
            {agreement.decision_kappa != null && (
              <span className="chip">decision κ {agreement.decision_kappa.toFixed(3)}</span>
            )}
            {agreement.scores.within_one_rate != null && (
              <span className="chip">
                ±1 {Math.round(agreement.scores.within_one_rate * 100)}%
              </span>
            )}
            {agreement.scores.quadratic_weighted_kappa != null && (
              <span className="chip">score κw {agreement.scores.quadratic_weighted_kappa.toFixed(3)}</span>
            )}
          </div>
          <p className="dim" style={{ marginBottom: 0, fontSize: "var(--fs-sm)" }}>
            {agreement.status === "insufficient_data"
              ? locale === "zh-CN"
                ? "至少需要同一 Run 的两位具名、不同评审人，才能计算一致性；同一人的重复评分不会冒充独立复评。"
                : "Agreement needs two distinct identified reviewers on the same run; repeat scores by one person are not treated as independent reviews."
              : locale === "zh-CN"
                ? `评分平均绝对差 ${agreement.scores.mean_absolute_difference?.toFixed(3) ?? "—"}；统计只使用同一 Run 内不同具名评审人的无序配对。`
                : `Score mean absolute difference ${agreement.scores.mean_absolute_difference?.toFixed(3) ?? "—"}; statistics use unordered within-run pairs of distinct identified reviewers only.`}
          </p>
        </div>
      )}
      {dimensions.length > 0 && (
        <table className="data" aria-label={locale === "zh-CN" ? "质量维度摘要" : "Quality dimension summary"}>
          <thead><tr>
            <th>{locale === "zh-CN" ? "维度" : "Dimension"}</th>
            <th className="num">{locale === "zh-CN" ? "平均" : "Average"}</th>
            <th className="num">{locale === "zh-CN" ? "最小" : "min"}</th><th className="num">{locale === "zh-CN" ? "最大" : "max"}</th>
          </tr></thead>
          <tbody>
            {dimensions.map(([key, value]) => (
              <tr key={key}>
                <td>{key.replaceAll("_", " ")}</td>
                <td className="num">{value.average.toFixed(2)}</td>
                <td className="num">{value.minimum}</td>
                <td className="num">{value.maximum}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {summary.by_pipeline.length > 0 && (
        <div className="row wrap" style={{ marginTop: 8 }}>
          {summary.by_pipeline.map((entry) => (
            <span className="chip" key={entry.label}>
              {entry.label}: {entry.average_overall_score.toFixed(2)} / 5 ({entry.reviewed_runs})
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function RunLauncher({ pipelines }: { pipelines: AgentPipeline[] }) {
  const startAgentRun = useStore((s) => s.startAgentRun);
  const document = useStore((s) => s.document);
  const skills = useStore((s) => s.skills);
  const enabledSkills = useStore((s) => s.enabledSkillIds);
  const toggleSkill = useStore((s) => s.toggleSkill);
  const analysis = useStore((s) => s.analysis);
  const stats = useStore((s) => s.stats);
  const locale = useStore((s) => s.locale);
  const project = useStore((s) => s.project);
  const reportError = useStore((s) => s.reportError);

  const [pipeline, setPipeline] = useState("full_auto");
  const [sectionKeys, setSectionKeys] = useState<string[]>([]);
  const [targetWords, setTargetWords] = useState(0);
  const [critique, setCritique] = useState(true);
  const [translate, setTranslate] = useState(false);
  const [maxPapers, setMaxPapers] = useState(40);
  const [maxNotes, setMaxNotes] = useState(25);
  const [preview, setPreview] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    if (project?.bilingual) setTranslate(true);
  }, [project?.bilingual]);

  const selected = pipelines.find((entry) => entry.id === pipeline);

  return (
    <div className="card">
      <div className="row wrap" style={{ marginBottom: 12 }}>
        {pipelines.map((entry) => (
          <button
            key={entry.id}
            className={`btn${pipeline === entry.id ? " primary" : ""}`}
            onClick={() => setPipeline(entry.id)}
            title={entry.description}
          >
            {locale === "zh-CN" ? entry.name_zh : entry.name}
          </button>
        ))}
      </div>

      {selected && (
        <>
          <p className="muted">
            {locale === "zh-CN" ? selected.description_zh : selected.description}
          </p>
          <div className="row wrap" style={{ gap: 5, marginBottom: 12 }}>
            {selected.steps.map((step, index) => (
              <span key={`${step}-${index}`} className="chip">
                {step}
              </span>
            ))}
            <span className="chip warn" title="Approximate number of model calls">
              ~{selected.typical_calls}
            </span>
          </div>
        </>
      )}

      {(pipeline === "section" || pipeline === "stitch") && document && (
        <div className="field">
          <label>
            {locale === "zh-CN" ? "选择章节" : "Sections"}{" "}
            <span className="dim">
              {sectionKeys.length === 0
                ? locale === "zh-CN" ? "（不选则处理全部）" : "(none selected = all)"
                : `(${sectionKeys.length})`}
            </span>
          </label>
          <div className="row wrap" style={{ gap: 5 }}>
            {document.sections.map((section) => (
              <button
                key={section.key}
                className={`chip clickable${sectionKeys.includes(section.key) ? " on" : ""}`}
                onClick={() =>
                  setSectionKeys((current) =>
                    current.includes(section.key)
                      ? current.filter((key) => key !== section.key)
                      : [...current, section.key],
                  )
                }
                title={locale === "zh-CN"
                  ? `${section.word_count} 字/词 · ${runStatusLabel(section.status, locale)}`
                  : `${section.word_count} words · ${section.status}`}
              >
                {locale === "zh-CN" && section.title_zh ? section.title_zh : section.title}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="row wrap">
        <div className="field" style={{ width: 130, marginBottom: 0 }}>
          <label>{locale === "zh-CN" ? "目标字数" : "Target words"}</label>
          <input
            type="number"
            value={targetWords || ""}
            placeholder="auto"
            onChange={(event) => setTargetWords(Number(event.target.value) || 0)}
          />
        </div>
        <div className="field" style={{ width: 150, marginBottom: 0 }}>
          <label title="How many papers go into each prompt">
            {locale === "zh-CN" ? "上下文论文数" : "Papers in context"}
          </label>
          <input
            type="number"
            value={maxPapers}
            onChange={(event) => setMaxPapers(Number(event.target.value) || 40)}
          />
        </div>
        <div className="field" style={{ width: 150, marginBottom: 0 }}>
          <label title="How many papers get individually read into notes">
            {locale === "zh-CN" ? "精读篇数" : "Papers to read"}
          </label>
          <input
            type="number"
            value={maxNotes}
            onChange={(event) => setMaxNotes(Number(event.target.value) || 25)}
          />
        </div>
        <label className="row" style={{ gap: 6, marginTop: 18 }}>
          <input
            type="checkbox"
            checked={critique}
            onChange={(event) => setCritique(event.target.checked)}
          />
          {locale === "zh-CN" ? "审阅并修订" : "Review and revise"}
        </label>
        <label className="row" style={{ gap: 6, marginTop: 18 }}>
          <input
            type="checkbox"
            checked={translate}
            onChange={(event) => setTranslate(event.target.checked)}
          />
          {locale === "zh-CN" ? "生成对照翻译" : "Produce the paired translation"}
        </label>
      </div>

      <div className="field" style={{ marginTop: 12 }}>
        <label>
          {locale === "zh-CN" ? "启用的技能" : "Active skills"}{" "}
          <span className="dim">({enabledSkills.length})</span>
        </label>
        <div className="row wrap" style={{ gap: 5 }}>
          {skills.map((skill) => (
            <button
              key={skill.id}
              className={`chip clickable${enabledSkills.includes(skill.id) ? " on" : ""}`}
              onClick={() => toggleSkill(skill.id)}
              title={skill.description}
            >
              {skill.name}
            </button>
          ))}
        </div>
        <span className="hint">
          {locale === "zh-CN"
            ? "技能会作为固定指令加入每个 agent 的 system prompt。"
            : "Skills are injected into each agent's system prompt as standing instructions."}
        </span>
      </div>

      {!analysis && (
        <p className="warn-text" style={{ fontSize: "var(--fs-sm)" }}>
          ⚠{" "}
          {locale === "zh-CN"
            ? "还没有研究图谱：缺口分析 agent 将只能依据文献本身判断，而没有量化的缺口候选。"
            : "No landscape yet — the gap-analysis agent will reason from the literature alone, without quantitative gap candidates."}
        </p>
      )}
      {stats && stats.papers_in_project === 0 && (
        <p className="err-text" style={{ fontSize: "var(--fs-sm)" }}>
          ⚠{" "}
          {locale === "zh-CN"
            ? "项目中没有论文，agent 将无法引用任何文献。请先执行检索。"
            : "This project has no papers, so the agents will have nothing to cite. Run a search first."}
        </p>
      )}

      <div className="row" style={{ marginTop: 12 }}>
        <button
          className="btn primary"
          onClick={() =>
            void startAgentRun({
              pipeline,
              section_keys: sectionKeys,
              target_words: targetWords || undefined,
              enable_critique: critique,
              enable_translation: translate,
              max_papers_in_context: maxPapers,
              max_notes: maxNotes,
            })
          }
        >
          {locale === "zh-CN" ? "开始运行" : "Start run"}
        </button>
        <button
          className="btn"
          onClick={async () => {
            try {
              setPreview(
                await endpoints.agents.preview({
                  project_id: project!.id,
                  role: "writer",
                  section_key: sectionKeys[0] ?? "",
                  skill_ids: enabledSkills,
                  analysis_id: analysis?.analysis_id ?? "",
                  max_papers_in_context: maxPapers,
                }),
              );
            } catch (error) {
              reportError(error, "previewing the prompt");
            }
          }}
          title={
            locale === "zh-CN"
              ? "查看 agent 实际会收到什么，不消耗模型调用"
              : "See exactly what the agent will be told, without spending a model call"
          }
        >
          {locale === "zh-CN" ? "预览提示词" : "Preview prompt"}
        </button>
      </div>

      {preview && <PromptPreview data={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}

function PromptPreview({
  data,
  onClose,
}: {
  data: Record<string, any>;
  onClose: () => void;
}) {
  const locale = useStore((s) => s.locale);
  const tokens = data.estimated_tokens as Record<string, number>;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <header>
          <span>
            {locale === "zh-CN" ? "提示词预览" : "Prompt preview"} · {String(data.role)}
          </span>
          <button className="btn icon sm" onClick={onClose} aria-label={locale === "zh-CN" ? "关闭提示词预览" : "Close prompt preview"}>
            ✕
          </button>
        </header>
        <div className="modal-body">
          <div className="row wrap" style={{ marginBottom: 12 }}>
            <span className="chip">system {tokens.system} tok</span>
            <span className="chip">user {tokens.user} tok</span>
            <span className="chip on">total ~{tokens.total} tok</span>
            <span className="chip">
              {(data.papers_in_context as any[]).length}{" "}
              {locale === "zh-CN" ? "篇论文在上下文中" : "papers in context"}
            </span>
            {(data.skills_used as string[]).map((skill) => (
              <span key={skill} className="chip ok">
                skill: {skill}
              </span>
            ))}
          </div>
          {(data.skill_problems as string[])?.map((problem, index) => (
            <p key={index} className="warn-text" style={{ fontSize: "var(--fs-sm)" }}>
              ⚠ {problem}
            </p>
          ))}

          <h3>{locale === "zh-CN" ? "系统提示" : "System prompt"}</h3>
          <pre
            className="mono"
            style={{ whiteSpace: "pre-wrap", background: "var(--bg-side)", padding: 10 }}
          >
            {String(data.system_prompt)}
          </pre>

          <h3>{locale === "zh-CN" ? "用户消息" : "User message"}</h3>
          <pre
            className="mono"
            style={{
              whiteSpace: "pre-wrap",
              background: "var(--bg-side)",
              padding: 10,
              maxHeight: 340,
              overflow: "auto",
            }}
          >
            {String(data.user_prompt)}
          </pre>
        </div>
      </div>
    </div>
  );
}

function RunDetail({ run }: { run: AgentRun }) {
  const locale = useStore((s) => s.locale);
  const cancelRun = useStore((s) => s.cancelRun);
  const startAgentRun = useStore((s) => s.startAgentRun);
  const setView = useStore((s) => s.setView);
  const [stepDetail, setStepDetail] = useState<Record<string, any> | null>(null);

  const warnings = (run.result?.warnings as string[]) ?? [];
  const failure = (run.result?.failure ?? {}) as Partial<LlmFailureDiagnostic>;
  const recovery = (run.result?.recovery ?? {}) as Record<string, unknown>;
  const snapshots = (run.result?.snapshots ?? {}) as Record<string, unknown>;

  function retryRun() {
    const legacyConfig =
      typeof run.request?.config === "object" && run.request.config
        ? (run.request.config as Record<string, unknown>)
        : {};
    const body: Record<string, unknown> = { ...legacyConfig, ...run.request };
    delete body.config;
    delete body.project_id;
    delete body.paper_count;
    if (!body.skill_ids && Array.isArray(body.skills)) body.skill_ids = body.skills;
    void startAgentRun(body);
  }

  return (
    <div className="card">
      <div className="row">
        <h3 className="grow" style={{ margin: 0 }}>
          {pipelineLabel(run.pipeline, locale)} · {runStatusLabel(run.status, locale)}
        </h3>
        {(run.status === "running" || run.status === "pending") && (
          <button className="btn sm danger" onClick={() => void cancelRun(run.id)}>
            {locale === "zh-CN" ? "取消" : "Cancel"}
          </button>
        )}
      </div>

      <div className="row wrap" style={{ gap: 5, margin: "8px 0" }}>
        <span className="chip">
          {run.tokens_in.toLocaleString()} in / {run.tokens_out.toLocaleString()} out
        </span>
        {run.cost_usd > 0 && <span className="chip">${run.cost_usd.toFixed(4)}</span>}
        {typeof run.result?.sections_written === "number" && (
          <span className="chip ok">
            {String(run.result.sections_written)}{" "}
            {locale === "zh-CN" ? "节已写入" : "sections written"}
          </span>
        )}
      </div>

      {run.error && <p className="err-text">{run.error}</p>}
      {run.status === "failed" && failure.outcome && (
        <div className="card" style={{ margin: "10px 0", borderColor: "var(--danger)" }}>
          <div className="row wrap">
            <span className="chip err">{failure.outcome.replaceAll("_", " ")}</span>
            {failure.provider && <span className="chip">{failure.provider}</span>}
            {failure.model && <span className="chip">{failure.model}</span>}
            {failure.http_status && <span className="chip">HTTP {failure.http_status}</span>}
            {failure.retry_after_s != null && (
              <span className="chip">retry after {failure.retry_after_s}s</span>
            )}
          </div>
          <p style={{ marginBottom: 4 }}>{failure.message || run.error}</p>
          {failure.context && <p className="muted">Context: {failure.context}</p>}
          {failure.hint && <p className="warn-text">{failure.hint}</p>}
          <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
            {locale === "zh-CN"
              ? `已完成步骤和已写章节均已保留。运行前恢复点：${String(snapshots.before || "不可用")}。`
              : `Completed steps and written sections were kept. Pre-run recovery point: ${String(snapshots.before || "unavailable")}.`}
          </p>
          <div className="row wrap">
            {Boolean(failure.retryable ?? recovery.retryable) && (
              <button className="btn primary sm" onClick={retryRun}>
                {locale === "zh-CN" ? "重试相同运行" : "Retry the same run"}
              </button>
            )}
            <button className="btn sm" onClick={() => setView("versions")}>
              {locale === "zh-CN" ? "比较或恢复快照" : "Compare or restore snapshot"}
            </button>
            {(failure.outcome === "configuration_error" ||
              failure.outcome === "authentication_error") && (
              <button className="btn sm" onClick={() => setView("settings")}>
                {locale === "zh-CN" ? "打开模型设置" : "Open model settings"}
              </button>
            )}
          </div>
        </div>
      )}
      {warnings.map((warning, index) => (
        <p key={index} className="warn-text" style={{ fontSize: "var(--fs-sm)" }}>
          ⚠ {warning}
        </p>
      ))}

      {run.status !== "pending" && run.status !== "running" && (
        <QualityPanel run={run} />
      )}

      <table
        className="data"
        aria-label={locale === "zh-CN" ? "智能体步骤" : "Agent steps"}
      >
        <thead>
          <tr>
            <th style={{ width: 30 }}>#</th>
            <th>{locale === "zh-CN" ? "角色" : "Agent"}</th>
            <th>{locale === "zh-CN" ? "状态" : "Status"}</th>
            <th className="num">{locale === "zh-CN" ? "令牌" : "tokens"}</th>
            <th className="num">{locale === "zh-CN" ? "毫秒" : "ms"}</th>
            <th>{locale === "zh-CN" ? "结果" : "Result"}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(run.steps ?? []).map((step, index) => (
            <tr key={step.id}>
              <td className="dim">{index + 1}</td>
              <td>{step.title || step.agent}</td>
              <td>
                <span
                  className={`chip ${
                    step.status === "done" ? "ok" : step.status === "failed" ? "err" : "on"
                  }`}
                >
                  {runStatusLabel(step.status, locale)}
                </span>
              </td>
              <td className="num">{step.tokens_in + step.tokens_out}</td>
              <td className="num">{step.duration_ms}</td>
              <td className="truncate" title={step.error || step.output}>
                {step.error ? (
                  <span className="err-text">
                    {String(
                      ((step.meta?.failure as Record<string, unknown> | undefined)?.outcome
                        ? `${(step.meta.failure as Record<string, unknown>).outcome}: `
                        : "") + step.error,
                    )}
                  </span>
                ) : (
                  step.output.slice(0, 120)
                )}
              </td>
              <td>
                <button
                  className="btn sm"
                  onClick={() =>
                    void endpoints.agents
                      .step(run.id, step.id)
                      .then(setStepDetail)
                      .catch((error) =>
                        useStore.getState().reportError(error, "loading the step"),
                      )
                  }
                >
                  {locale === "zh-CN" ? "审查" : "Audit"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {stepDetail && (
        <div className="modal-backdrop" onClick={() => setStepDetail(null)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <header>
              <span>
                {String(stepDetail.title || stepDetail.agent)} ·{" "}
                {String(stepDetail.model)}
              </span>
              <button className="btn icon sm" onClick={() => setStepDetail(null)} aria-label={locale === "zh-CN" ? "关闭步骤审计" : "Close step audit"}>
                ✕
              </button>
            </header>
            <div className="modal-body">
              <h3>{locale === "zh-CN" ? "发送的提示词" : "Prompt sent"}</h3>
              <pre
                className="mono"
                style={{
                  whiteSpace: "pre-wrap",
                  background: "var(--bg-side)",
                  padding: 10,
                  maxHeight: 300,
                  overflow: "auto",
                }}
              >
                {String(stepDetail.prompt || "(pruned to save space)")}
              </pre>
              <h3>{locale === "zh-CN" ? "模型输出" : "Model output"}</h3>
              <pre
                className="mono"
                style={{
                  whiteSpace: "pre-wrap",
                  background: "var(--bg-side)",
                  padding: 10,
                  maxHeight: 340,
                  overflow: "auto",
                }}
              >
                {String(stepDetail.output || "(empty)")}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const QUALITY_DIMENSIONS = [
  ["factual_grounding", "事实支撑", "Factual grounding"],
  ["citation_support", "引用支撑", "Citation support"],
  ["methodological_soundness", "方法可靠性", "Methodological soundness"],
  ["literature_coverage", "文献覆盖", "Literature coverage"],
  ["argument_coherence", "论证连贯性", "Argument coherence"],
  ["writing_clarity", "写作清晰度", "Writing clarity"],
] as const;

function QualityPanel({ run }: { run: AgentRun }) {
  const locale = useStore((s) => s.locale);
  const openRun = useStore((s) => s.openRun);
  const loadRuns = useStore((s) => s.loadRuns);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const persistedQuality = run.result?.quality_report as AgentQualityReport | undefined;
  const persistedManuscript = run.result?.review_manuscript as
    | AgentReviewManuscript
    | undefined;
  const evaluations = (
    Array.isArray(run.result?.human_evaluations) ? run.result.human_evaluations : []
  ) as AgentHumanEvaluation[];
  const [reviewer, setReviewer] = useState("");
  const [decision, setDecision] = useState<AgentEvaluationRequest["decision"]>(
    "revision_required",
  );
  const [scores, setScores] = useState<Record<string, number>>(
    Object.fromEntries(QUALITY_DIMENSIONS.map(([key]) => [key, 3])),
  );
  const [sourceChecked, setSourceChecked] = useState(false);
  const [warningsAcknowledged, setWarningsAcknowledged] = useState(false);
  const [reviewedSections, setReviewedSections] = useState<string[]>([]);
  const [reviewedPapers, setReviewedPapers] = useState<string[]>([]);
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [reviewPacket, setReviewPacket] = useState<AgentReviewPacket | null>(null);
  const [blindMode, setBlindMode] = useState(false);
  const [packetLoading, setPacketLoading] = useState(false);
  const [exporting, setExporting] = useState<"blind" | "analysis" | "">("");
  const quality = blindMode
    ? reviewPacket?.automatic_quality_report
    : persistedQuality;
  const manuscript = reviewPacket?.manuscript ?? persistedManuscript;
  const manuscriptFingerprint = manuscript?.manuscript_fingerprint ?? "";
  const immutableEvidenceReady = Boolean(
    quality
    && quality.schema_version >= 2
    && manuscriptFingerprint
    && manuscript?.sections?.length
    && reviewPacket?.evidence_contract?.manuscript_integrity === "pass",
  );

  useEffect(() => {
    setReviewer("");
    setDecision("revision_required");
    setScores(Object.fromEntries(QUALITY_DIMENSIONS.map(([key]) => [key, 3])));
    setSourceChecked(false);
    setWarningsAcknowledged(false);
    setReviewedSections([]);
    setReviewedPapers([]);
    setNotes("");
    setReviewPacket(null);
    setBlindMode(false);
    let active = true;
    void endpoints.agents.reviewPacket(run.id, "blind")
      .then((packet) => {
        if (active) setReviewPacket(packet);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [run.id]);

  const gate = quality?.gate ?? "unavailable";
  const gateLabel =
    locale === "zh-CN"
      ? { pass: "通过", warn: "需注意", fail: "未通过", unavailable: "不可用" }[gate]
      : gate.replaceAll("_", " ");
  const requiredSectionKeys = quality?.review_requirements?.required_section_keys
    ?? (quality?.sections ?? [])
      .filter((section) => Boolean(section.modified_by_run))
      .map((section) => section.section_key)
      .filter(Boolean);
  const requiredPaperIds = quality?.review_requirements?.required_paper_ids
    ?? quality?.citation_registry?.cited_paper_ids
    ?? [];
  const evidencePapers = quality?.citation_registry?.papers ?? [];
  const missingSections = requiredSectionKeys.filter((key) => !reviewedSections.includes(key));
  const missingPapers = requiredPaperIds.filter((paperId) => !reviewedPapers.includes(paperId));
  const acceptanceBlockers = decision !== "accepted" ? [] : [
    ...(run.status !== "done" ? [locale === "zh-CN" ? "运行未成功完成" : "run is not done"] : []),
    ...(!quality ? [locale === "zh-CN" ? "缺少自动质量报告" : "automatic report is missing"] : []),
    ...(!immutableEvidenceReady ? [locale === "zh-CN" ? "缺少可验证的冻结正文" : "immutable manuscript evidence is missing"] : []),
    ...(!["pass", "warn"].includes(gate) ? [locale === "zh-CN" ? "自动结构门禁未通过" : "automatic structural gate is not eligible"] : []),
    ...(!requiredSectionKeys.length ? [locale === "zh-CN" ? "本轮没有可验收的修改章节" : "the run has no modified section to accept"] : []),
    ...(missingSections.length ? [locale === "zh-CN" ? `尚未逐节核对：${missingSections.join("、")}` : `sections not reviewed: ${missingSections.join(", ")}`] : []),
    ...(missingPapers.length ? [locale === "zh-CN" ? `尚未逐篇核对 ${missingPapers.length} 个引用来源` : `${missingPapers.length} cited source(s) not reviewed`] : []),
    ...(!sourceChecked ? [locale === "zh-CN" ? "未确认来源证据核验" : "source evidence is not confirmed"] : []),
    ...(gate === "warn" && !warningsAcknowledged ? [locale === "zh-CN" ? "未确认已处理自动警告" : "automatic warnings are not acknowledged"] : []),
    ...(QUALITY_DIMENSIONS.some(([key]) => (scores[key] ?? 0) < 3) ? [locale === "zh-CN" ? "至少一项评分低于 3" : "at least one score is below 3"] : []),
    ...(!reviewer.trim() ? [locale === "zh-CN" ? "缺少评审人" : "reviewer is required"] : []),
    ...(notes.trim().length < 20 ? [locale === "zh-CN" ? "证据说明至少需要 20 个字符" : "evidence notes need at least 20 characters"] : []),
  ];
  const acceptanceBlocked = acceptanceBlockers.length > 0;

  function toggleReviewed(
    setter: Dispatch<SetStateAction<string[]>>,
    value: string,
    checked: boolean,
  ) {
    setter((current) =>
      checked
        ? current.includes(value) ? current : [...current, value]
        : current.filter((item) => item !== value),
    );
  }

  async function saveEvaluation() {
    setSaving(true);
    try {
      await endpoints.agents.evaluate(run.id, {
        reviewer,
        decision,
        factual_grounding: scores.factual_grounding,
        citation_support: scores.citation_support,
        methodological_soundness: scores.methodological_soundness,
        literature_coverage: scores.literature_coverage,
        argument_coherence: scores.argument_coherence,
        writing_clarity: scores.writing_clarity,
        source_evidence_checked: sourceChecked,
        automatic_warnings_acknowledged: warningsAcknowledged,
        reviewed_section_keys: reviewedSections,
        reviewed_paper_ids: reviewedPapers,
        reviewed_manuscript_fingerprint: manuscriptFingerprint,
        review_mode: blindMode ? "blind" : "identified",
        notes,
      });
      await Promise.all([openRun(run.id), loadRuns()]);
      notify({
        kind: "success",
        message: locale === "zh-CN" ? "人工质量评审已保存" : "Human quality review saved",
        detail:
          locale === "zh-CN"
            ? "评审以追加记录保存，不会覆盖此前结论。"
            : "The review was appended; earlier decisions were not overwritten.",
      });
      setNotes("");
      setBlindMode(false);
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "保存人工质量评审" : "saving quality review");
    } finally {
      setSaving(false);
    }
  }

  async function enterBlindReview() {
    setPacketLoading(true);
    try {
      const packet = reviewPacket ?? await endpoints.agents.reviewPacket(run.id, "blind");
      setReviewPacket(packet);
      setReviewer("");
      setDecision("revision_required");
      setScores(Object.fromEntries(QUALITY_DIMENSIONS.map(([key]) => [key, 3])));
      setSourceChecked(false);
      setWarningsAcknowledged(false);
      setReviewedSections([]);
      setReviewedPapers([]);
      setNotes("");
      setBlindMode(true);
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "打开盲评" : "opening blind review");
    } finally {
      setPacketLoading(false);
    }
  }

  async function exportPacket(kind: "blind" | "analysis") {
    setExporting(kind);
    try {
      const result = await endpoints.agents.exportReviewPacket(run.id, kind);
      if (!blindMode) void window.papercreator?.shell.showItem(result.path);
      notify({
        kind: "success",
        message: locale === "zh-CN" ? "评审证据包已导出" : "Review evidence packet exported",
        detail: blindMode
          ? `${result.sample_id} · ${result.packet_fingerprint.slice(0, 12)}`
          : `${result.sample_id} · ${result.packet_fingerprint.slice(0, 12)} · ${result.path}`,
      });
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "导出评审证据包" : "exporting review packet");
    } finally {
      setExporting("");
    }
  }

  return (
    <section
      className="card"
      aria-label={locale === "zh-CN" ? "论文质量报告" : "Manuscript quality report"}
      style={blindMode ? {
        position: "fixed",
        inset: 0,
        zIndex: 2000,
        margin: 0,
        padding: 24,
        overflow: "auto",
        background: "var(--bg-main)",
      } : { margin: "12px 0", background: "var(--bg-side)" }}
    >
      <div className="row wrap">
        <h3 className="grow" style={{ margin: 0 }}>
          {blindMode
            ? `${locale === "zh-CN" ? "盲评样本" : "Blind review sample"} ${reviewPacket?.sample_id ?? ""}`
            : locale === "zh-CN" ? "论文质量报告" : "Manuscript quality report"}
        </h3>
        {!blindMode && (
          <button className="btn sm" disabled={packetLoading} onClick={() => void enterBlindReview()}>
            {packetLoading ? "…" : locale === "zh-CN" ? "进入盲评" : "Enter blind review"}
          </button>
        )}
        {blindMode && (
          <button className="btn sm" onClick={() => setBlindMode(false)}>
            {locale === "zh-CN" ? "退出盲评" : "Exit blind review"}
          </button>
        )}
        <button className="btn sm" disabled={Boolean(exporting)} onClick={() => void exportPacket("blind")}>
          {exporting === "blind" ? "…" : locale === "zh-CN" ? "导出盲评包" : "Export blind packet"}
        </button>
        {!blindMode && (
          <button className="btn sm" disabled={Boolean(exporting)} onClick={() => void exportPacket("analysis")}>
            {exporting === "analysis" ? "…" : locale === "zh-CN" ? "导出分析包" : "Export analysis packet"}
          </button>
        )}
        <span className={`chip ${gate === "pass" ? "ok" : gate === "fail" ? "err" : "on"}`}>
          {locale === "zh-CN" ? "自动门禁：" : "Automatic gate: "}{gateLabel}
        </span>
        {!blindMode && quality?.acceptance?.latest_human_decision && (
          <span className="chip">
            {locale === "zh-CN" ? "人工：" : "Human: "}
            {quality.acceptance.latest_human_decision.replaceAll("_", " ")}
          </span>
        )}
      </div>

      {blindMode && (
        <div className="card" style={{ margin: "10px 0", background: "var(--bg-side)" }}>
          <strong>{locale === "zh-CN" ? "身份隔离已启用" : "Identity isolation is active"}</strong>
          <p className="muted" style={{ marginBottom: 0 }}>
            {locale === "zh-CN"
              ? "此全屏视图不显示项目、Run、流水线、模型、成本、既有人工结论或本地 PDF 路径。它减少锚定偏差，但不能证明评审人在进入前未看过身份信息。"
              : "This full-screen view hides project, run, pipeline, model, cost, prior human decisions and local PDF paths. It reduces anchoring bias, but cannot prove the reviewer had no prior identity exposure."}
          </p>
        </div>
      )}

      {quality ? (
        <>
          <div className="row wrap" style={{ gap: 5, margin: "8px 0" }}>
            <span className="chip">{quality.metrics?.words ?? 0} words</span>
            <span className="chip">
              {quality.metrics?.cited_papers ?? 0} {locale === "zh-CN" ? "篇被引论文" : "cited papers"}
            </span>
            <span className="chip">
              {quality.metrics?.citation_marker_occurrences ?? 0} {locale === "zh-CN" ? "处引用" : "citation markers"}
            </span>
            <span className={`chip ${(quality.metrics?.invalid_citation_keys ?? 0) ? "err" : "ok"}`}>
              {quality.metrics?.invalid_citation_keys ?? 0} {locale === "zh-CN" ? "个无效键" : "invalid keys"}
            </span>
          </div>
          <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
            {locale === "zh-CN"
              ? "自动检查只能证明引用键、元数据和结构是否一致，不能证明论断真实。接受稿件前仍需人工打开来源核验。"
              : "Automation proves key, metadata and structural integrity—not factual truth. Open the sources and complete a human review before acceptance."}
          </p>
          <table
            className="data"
            aria-label={locale === "zh-CN" ? "自动质量检查" : "Automatic quality checks"}
          >
            <thead>
              <tr>
                <th>{locale === "zh-CN" ? "检查" : "Check"}</th>
                <th>{locale === "zh-CN" ? "状态" : "Status"}</th>
                <th>{locale === "zh-CN" ? "证据" : "Evidence"}</th>
              </tr>
            </thead>
            <tbody>
              {(quality.checks ?? []).map((check) => (
                <tr key={check.id}>
                  <td>{check.id.replaceAll("_", " ")}</td>
                  <td>
                    <span className={`chip ${check.status === "pass" ? "ok" : check.status === "fail" ? "err" : "on"}`}>
                      {check.status.replaceAll("_", " ")}
                    </span>
                  </td>
                  <td>{check.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : (
        <p className="muted">
          {locale === "zh-CN"
            ? "这是旧运行，没有自动质量报告；仍可追加“需要修订/拒绝”评审，但不能按 v3 证据合同接受。"
            : "This legacy run has no automatic report. Revision/rejection reviews remain appendable, but rubric v3 cannot accept it."}
        </p>
      )}

      <div className={`card ${immutableEvidenceReady ? "" : "warn-text"}`} style={{ margin: "10px 0" }}>
        <div className="row wrap">
          <strong>{locale === "zh-CN" ? "冻结正文证据" : "Frozen manuscript evidence"}</strong>
          <span className={`chip ${immutableEvidenceReady ? "ok" : "err"}`}>
            {immutableEvidenceReady ? "verified contract" : "legacy / unavailable"}
          </span>
          {manuscriptFingerprint && (
            <span className="mono dim" title={manuscriptFingerprint}>
              SHA-256 {manuscriptFingerprint.slice(0, 16)}
            </span>
          )}
          {reviewPacket?.packet_fingerprint && (
            <span className="mono dim" title={reviewPacket.packet_fingerprint}>
              packet {reviewPacket.packet_fingerprint.slice(0, 12)}
            </span>
          )}
          <span className="chip">
            rubric v{reviewPacket?.evidence_contract?.rubric_version
              ?? quality?.review_requirements?.rubric_version
              ?? 2}
          </span>
          {manuscript?.source_snapshot_id && !blindMode && (
            <span className="chip">snapshot {manuscript.source_snapshot_id}</span>
          )}
        </div>
        <p className="dim" style={{ marginBottom: 0 }}>
          {locale === "zh-CN"
            ? "本评审只绑定下方这份不可变正文；项目中的当前正文即使后来修改，也不会替换该证据。"
            : "This review binds only to the immutable prose below; later edits to the live project cannot replace this evidence."}
        </p>
      </div>

      <h4>{locale === "zh-CN" ? "阅读冻结正文" : "Read the frozen manuscript"}</h4>
      {manuscript?.sections?.length ? manuscript.sections.map((section) => (
        <details key={section.section_key} open={section.modified_by_run} style={{ margin: "7px 0" }}>
          <summary>
            <strong>{section.title || section.section_key}</strong>{" "}
            {section.modified_by_run && <span className="chip on">{locale === "zh-CN" ? "本次运行已修改" : "modified by run"}</span>}{" "}
            <span className="mono dim">{section.primary_text_sha256.slice(0, 12)}</span>
          </summary>
          <pre style={{ whiteSpace: "pre-wrap", maxHeight: 360, overflow: "auto" }}>
            {section.primary_text || (locale === "zh-CN" ? "（空）" : "(empty)")}
          </pre>
          {section.paired_text && (
            <details>
              <summary>{locale === "zh-CN" ? "对照语言正文" : "Paired-language text"}</summary>
              <pre style={{ whiteSpace: "pre-wrap", maxHeight: 280, overflow: "auto" }}>
                {section.paired_text}
              </pre>
            </details>
          )}
        </details>
      )) : (
        <p className="warn-text">
          {locale === "zh-CN" ? "该历史 Run 没有可重放的正文快照。" : "This historical run has no replayable manuscript snapshot."}
        </p>
      )}

      <h4>{locale === "zh-CN" ? "确认逐节核对" : "Confirm each modified section"}</h4>
      {requiredSectionKeys.length ? (
        <div className="grid two">
          {requiredSectionKeys.map((sectionKey) => {
            const section = quality?.sections.find((item) => item.section_key === sectionKey);
            return (
              <label className="row" key={sectionKey}>
                <input
                  type="checkbox"
                  aria-label={`${locale === "zh-CN" ? "已核对章节" : "Reviewed section"} ${sectionKey}`}
                  checked={reviewedSections.includes(sectionKey)}
                  onChange={(event) =>
                    toggleReviewed(setReviewedSections, sectionKey, event.target.checked)}
                />
                <span>
                  <strong>{sectionKey}</strong>{" "}
                  <span className="dim">
                    {section?.words ?? 0} words · {section?.citation_occurrences ?? 0} citations
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      ) : (
        <p className="dim">
          {locale === "zh-CN" ? "本轮没有登记修改章节，不能作为完成稿接受。" : "No modified section was recorded; this run cannot be accepted as completed output."}
        </p>
      )}

      <h4>{locale === "zh-CN" ? "逐篇核对被引来源" : "Review every cited source"}</h4>
      {requiredPaperIds.length ? (
        <div>
          {requiredPaperIds.map((paperId) => {
            const paper = evidencePapers.find((item) => item.paper_id === paperId);
            const sourceUrl = paper?.url || (paper?.doi ? `https://doi.org/${paper.doi}` : "");
            return (
              <div className="row wrap" key={paperId} style={{ margin: "5px 0" }}>
                <label className="row grow">
                  <input
                    type="checkbox"
                    aria-label={`${locale === "zh-CN" ? "已核对来源" : "Reviewed source"} ${paper?.key || paperId}`}
                    checked={reviewedPapers.includes(paperId)}
                    onChange={(event) =>
                      toggleReviewed(setReviewedPapers, paperId, event.target.checked)}
                  />
                  <span>
                    <strong>[{paper?.key || paperId}]</strong>{" "}
                    {paper?.title || paperId}{paper?.year ? ` (${paper.year})` : ""}
                  </span>
                </label>
                {(paper?.pdf_path || sourceUrl) && (
                  <button
                    className="btn sm"
                    onClick={() => {
                      if (paper?.pdf_path) void window.papercreator?.shell.openPath(paper.pdf_path);
                      else if (sourceUrl) void window.papercreator?.shell.openExternal(sourceUrl);
                    }}
                  >
                    {locale === "zh-CN" ? "打开来源" : "Open source"}
                  </button>
                )}
                {!paper?.abstract_available && (
                  <span className="chip on">{locale === "zh-CN" ? "无摘要证据" : "no abstract evidence"}</span>
                )}
                {paper?.abstract && (
                  <details style={{ width: "100%", marginLeft: 24 }}>
                    <summary>{locale === "zh-CN" ? "查看冻结摘要证据" : "Read frozen abstract evidence"}</summary>
                    <p>{paper.abstract}</p>
                    {paper.abstract_sha256 && (
                      <span className="mono dim">SHA-256 {paper.abstract_sha256.slice(0, 16)}</span>
                    )}
                  </details>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="dim">{locale === "zh-CN" ? "本轮正文没有解析到被引论文。" : "No cited project paper was resolved for this run."}</p>
      )}

      <h4>{locale === "zh-CN" ? "追加人工 Rubric" : "Append human rubric"}</h4>
      <div className="grid two">
        {QUALITY_DIMENSIONS.map(([key, zh, en]) => (
          <label className="field" key={key}>
            <span>{locale === "zh-CN" ? zh : en}</span>
            <select
              aria-label={locale === "zh-CN" ? zh : en}
              value={scores[key] ?? 3}
              onChange={(event) =>
                setScores((current) => ({ ...current, [key]: Number(event.target.value) }))
              }
            >
              {[1, 2, 3, 4, 5].map((score) => (
                <option key={score} value={score}>{score} / 5</option>
              ))}
            </select>
          </label>
        ))}
        <label className="field">
          <span>{locale === "zh-CN" ? "评审结论" : "Decision"}</span>
          <select
            aria-label={locale === "zh-CN" ? "评审结论" : "Decision"}
            value={decision}
            onChange={(event) => setDecision(event.target.value as AgentEvaluationRequest["decision"])}
          >
            <option value="revision_required">{locale === "zh-CN" ? "需要修订" : "Revision required"}</option>
            <option
              value="accepted"
              disabled={run.status !== "done" || !quality || !immutableEvidenceReady || !["pass", "warn"].includes(gate) || !requiredSectionKeys.length}
            >
              {locale === "zh-CN" ? "已接受" : "Accepted"}
            </option>
            <option value="rejected">{locale === "zh-CN" ? "拒绝" : "Rejected"}</option>
          </select>
        </label>
        <label className="field">
          <span>
            {blindMode
              ? locale === "zh-CN" ? "匿名评审编号（接受时必填）" : "Anonymous reviewer code (required for acceptance)"
              : locale === "zh-CN" ? "评审人（接受时必填）" : "Reviewer (required for acceptance)"}
          </span>
          <input
            aria-label={blindMode
              ? locale === "zh-CN" ? "匿名评审编号" : "Anonymous reviewer code"
              : locale === "zh-CN" ? "评审人" : "Reviewer"}
            value={reviewer}
            maxLength={120}
            onChange={(event) => setReviewer(event.target.value)}
          />
        </label>
      </div>
      <label className="row" style={{ margin: "8px 0" }}>
        <input
          type="checkbox"
          checked={sourceChecked}
          onChange={(event) => setSourceChecked(event.target.checked)}
        />
        <span>
          {locale === "zh-CN"
            ? "我已打开并核对引用来源，而不是只阅读模型生成的摘要"
            : "I opened and checked the cited sources, not only model-generated summaries"}
        </span>
      </label>
      {gate === "warn" && (
        <label className="row" style={{ margin: "8px 0" }}>
          <input
            type="checkbox"
            checked={warningsAcknowledged}
            onChange={(event) => setWarningsAcknowledged(event.target.checked)}
          />
          <span>
            {locale === "zh-CN"
              ? "我已阅读自动警告，并在证据说明中记录处理结论"
              : "I reviewed the automatic warnings and recorded their disposition in the evidence notes"}
          </span>
        </label>
      )}
      <label className="field">
        <span>{locale === "zh-CN" ? "证据与修订说明" : "Evidence and revision notes"}</span>
        <textarea
          aria-label={locale === "zh-CN" ? "证据与修订说明" : "Evidence and revision notes"}
          value={notes}
          maxLength={8000}
          rows={3}
          onChange={(event) => setNotes(event.target.value)}
        />
      </label>
      {acceptanceBlocked && (
        <div className="warn-text">
          <strong>{locale === "zh-CN" ? "当前不能接受：" : "Acceptance is blocked:"}</strong>
          <ul>
            {acceptanceBlockers.map((blocker) => <li key={blocker}>{blocker}</li>)}
          </ul>
        </div>
      )}
      <button
        className="btn primary sm"
        disabled={saving || acceptanceBlocked}
        onClick={() => void saveEvaluation()}
      >
        {saving
          ? locale === "zh-CN" ? "保存中…" : "Saving…"
          : locale === "zh-CN" ? "保存人工评审" : "Save human review"}
      </button>

      {!blindMode && evaluations.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <h4>{locale === "zh-CN" ? "历史评审（只追加）" : "Review history (append-only)"}</h4>
          {[...evaluations].reverse().map((evaluation) => (
            <div key={evaluation.id} className="row wrap" style={{ margin: "5px 0" }}>
              <span className={`chip ${evaluation.decision === "accepted" ? "ok" : evaluation.decision === "rejected" ? "err" : "on"}`}>
                {evaluation.decision.replaceAll("_", " ")}
              </span>
              <span>{evaluation.overall_score.toFixed(2)} / 5</span>
              <span className="chip">rubric v{evaluation.rubric_version}</span>
              <span className="dim">{evaluation.reviewer || (locale === "zh-CN" ? "本地评审人" : "local reviewer")}</span>
              <span className="dim">{evaluation.created_at.slice(0, 16)}</span>
              {evaluation.review_target?.quality_report_fingerprint && (
                <span className="mono dim" title={evaluation.review_target.quality_report_fingerprint}>
                  evidence {evaluation.review_target.quality_report_fingerprint.slice(0, 12)}
                </span>
              )}
              {evaluation.review_target?.manuscript_fingerprint && (
                <span className="mono dim" title={evaluation.review_target.manuscript_fingerprint}>
                  manuscript {evaluation.review_target.manuscript_fingerprint.slice(0, 12)}
                </span>
              )}
              {evaluation.review_mode && <span className="chip">{evaluation.review_mode}</span>}
              <span className="dim">
                {(evaluation.reviewed_section_keys ?? []).length} sections ·{" "}
                {(evaluation.reviewed_paper_ids ?? []).length} sources
              </span>
              {evaluation.notes && <span>{evaluation.notes}</span>}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function RoleReference({ roles }: { roles: AgentRole[] }) {
  const locale = useStore((s) => s.locale);
  if (!roles.length) return null;
  return (
    <>
      <h2>{locale === "zh-CN" ? "角色说明" : "The roles"}</h2>
      <table className="data">
        <thead>
          <tr>
            <th>{locale === "zh-CN" ? "角色" : "Role"}</th>
            <th>{locale === "zh-CN" ? "职责" : "What it does"}</th>
            <th>{locale === "zh-CN" ? "依赖" : "Needs"}</th>
          </tr>
        </thead>
        <tbody>
          {roles.map((role) => (
            <tr key={role.name}>
              <td>
                {locale === "zh-CN" && role.title_zh ? role.title_zh : role.title}
                {role.per_section && (
                  <span className="chip" style={{ marginLeft: 6 }}>
                    per section
                  </span>
                )}
              </td>
              <td className="muted">{role.description}</td>
              <td className="dim mono">{role.requires.join(", ") || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

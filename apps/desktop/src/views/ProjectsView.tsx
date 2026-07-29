/**
 * Projects view: create, open, import.
 *
 * The creation form asks for the research idea up front, because that single
 * field drives three later features - idea-based retrieval, gap positioning, and
 * the planner agent's contribution claim. Leaving it for later means those
 * features start from nothing.
 */

import { useEffect, useId, useState } from "react";

import * as endpoints from "../api/endpoints";
import { WorkbenchPanel } from "../components/WorkbenchPanel";
import { trapDialogFocus } from "../components/dialogFocus";
import { useStore } from "../state/store";
import type { PaperTemplate } from "../api/types";

export function ProjectsView() {
  const projects = useStore((s) => s.projects);
  const importable = useStore((s) => s.importable);
  const openProject = useStore((s) => s.openProject);
  const loadProjects = useStore((s) => s.loadProjects);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const locale = useStore((s) => s.locale);
  const creating = useStore((s) => s.projectCreatorOpen);
  const openCreator = useStore((s) => s.openProjectCreator);
  const closeCreator = useStore((s) => s.closeProjectCreator);

  return (
    <div className="view">
      <div className="row">
        <div className="grow">
          <h1>{locale === "zh-CN" ? "PaperCreator 工作台" : "PaperCreator workbench"}</h1>
          <p className="sub">
            {locale === "zh-CN"
              ? "在一个可迁移的工作台中分类管理研究资料，并创建带 Git、文献集合和 AI 写作流程的论文项目。"
              : "Classify research material in one portable workbench, then create paper projects with Git, collections and AI writing workflows."}
          </p>
        </div>
        <button className="btn primary" onClick={openCreator}>
          {locale === "zh-CN" ? "新建项目" : "New project"}
        </button>
      </div>

      {creating && <CreateProjectModal onClose={closeCreator} />}

      <WorkbenchPanel onNewProject={openCreator} />

      {importable.length > 0 && (
        <div className="card">
          <h3>{locale === "zh-CN" ? "可导入的目录" : "Importable directories"}</h3>
          <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
            {locale === "zh-CN"
              ? "工作区中发现了带 project.json 但数据库中不存在的项目。可能来自数据库丢失或从另一台机器复制。"
              : "Found workspace directories with a project.json but no database row — from a lost database, or copied from another machine."}
          </p>
          {importable.map((entry) => (
            <div key={String(entry.path)} className="row" style={{ padding: "3px 0" }}>
              <button
                className="btn sm"
                onClick={async () => {
                  try {
                    const result = await endpoints.projects.importFromDisk(String(entry.path));
                    await loadProjects();
                    notify({
                      kind: "success",
                      message: locale === "zh-CN" ? `已导入 ${String(entry.title ?? entry.slug)}` : `Imported ${String(entry.title ?? entry.slug)}`,
                      detail: (result.warnings as string[])?.join("; "),
                    });
                  } catch (error) {
                    reportError(error, locale === "zh-CN" ? "导入项目" : "importing the project");
                  }
                }}
              >
                {locale === "zh-CN" ? "导入" : "Import"}
              </button>
              <span className="grow truncate">{String(entry.title ?? entry.slug)}</span>
              <span className="dim mono">{String(entry.path)}</span>
            </div>
          ))}
        </div>
      )}

      <div className="row" style={{ marginTop: 18 }}>
        <div className="grow">
          <h2>{locale === "zh-CN" ? "论文项目" : "Paper projects"}</h2>
          <p className="sub">
            {locale === "zh-CN"
              ? "每个项目拥有独立的手稿、章节、文献集合、分析结果、导出产物和 Git 历史。"
              : "Each project owns its manuscript, sections, collections, analyses, exports and Git history."}
          </p>
        </div>
      </div>

      {projects.length === 0 ? (
        <div className="empty">
          <div className="big">◱</div>
          <div>
            {locale === "zh-CN" ? "还没有项目" : "No projects yet"}
          </div>
          <p className="dim">
            {locale === "zh-CN"
              ? "新建一个项目，填入你的研究想法，就可以开始检索文献。"
              : "Create one with your research idea, then start retrieving literature."}
          </p>
        </div>
      ) : (
        <div className="project-grid">
          {projects.map((project) => (
            <div
              key={project.id}
              className="project-card clickable"
              onClick={() => void openProject(project.id)}
            >
              <h3>{project.title}</h3>
              {project.title_zh && <div className="dim">{project.title_zh}</div>}
              <p className="muted" style={{ fontSize: "var(--fs-sm)", minHeight: 34 }}>
                {project.idea || project.description || (
                  <span className="dim">
                    {locale === "zh-CN" ? "（无描述）" : "(no description)"}
                  </span>
                )}
              </p>
              <div className="row wrap" style={{ gap: 6 }}>
                <span className="chip">
                  {project.paper_count} {locale === "zh-CN" ? "篇文献" : "papers"}
                </span>
                <span className="chip">
                  {project.word_count.toLocaleString()} {locale === "zh-CN" ? "词" : "words"}
                </span>
                <span className="chip">
                  {project.section_count} {locale === "zh-CN" ? "章节" : "sections"}
                </span>
                <span className="chip">{project.template_id}</span>
                <span className="chip">{project.language}</span>
                {project.bilingual && (
                  <span className="chip">{locale === "zh-CN" ? "双语" : "bilingual"}</span>
                )}
                {project.git_enabled && (
                  <span className="chip ok">{locale === "zh-CN" ? "本地 Git" : "local Git"}</span>
                )}
              </div>
              <div className="project-card-footer">
                <span className="dim">
                  {locale === "zh-CN" ? "更新于 " : "Updated "}
                  {new Date(project.updated_at).toLocaleString(locale)}
                </span>
                <span className="btn sm primary">
                  {locale === "zh-CN" ? "进入项目 →" : "Open project →"}
                </span>
              </div>
              <div
                className="dim mono truncate"
                style={{ marginTop: 8, fontSize: "var(--fs-xs)" }}
                title={project.path}
              >
                {project.path}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CreateProjectModal({ onClose }: { onClose: () => void }) {
  const locale = useStore((s) => s.locale);
  const loadProjects = useStore((s) => s.loadProjects);
  const openProject = useStore((s) => s.openProject);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const formId = useId();

  const [templates, setTemplates] = useState<PaperTemplate[]>([]);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    title: "",
    title_zh: "",
    idea: "",
    research_field: "",
    target_venue: "",
    template_id: "generic",
    language: "en",
    bilingual: true,
    citation_style: "ieee",
    git_enabled: true,
    target_words: 0,
  });

  useEffect(() => {
    void endpoints.writing
      .templates()
      .then((result) => setTemplates(result.items))
      .catch(() => setTemplates([]));
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, onClose]);

  const template = templates.find((entry) => entry.id === form.template_id);

  async function submit() {
    if (!form.title.trim()) {
      notify({ kind: "warning", message: locale === "zh-CN" ? "必须填写标题" : "A title is required" });
      return;
    }
    setBusy(true);
    try {
      const result = await endpoints.projects.create({ ...form, apply_template: true });
      await loadProjects();
      await openProject(result.project.id);
      notify({
        kind: "success",
        message:
          locale === "zh-CN"
            ? `已创建“${result.project.title}”`
            : `Created "${result.project.title}"`,
        detail: result.git?.created
          ? locale === "zh-CN"
            ? "本地 Git 已初始化；不会自动联网或推送。"
            : "Local Git initialised; it will not connect or push automatically."
          : result.git?.reason
            ? `Git: ${result.git.reason}`
            : undefined,
      });
      onClose();
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "创建项目" : "creating the project");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onClose}>
      <div
        className="modal"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${formId}-title`}
        onKeyDown={trapDialogFocus}
      >
        <header>
          <span id={`${formId}-title`}>{locale === "zh-CN" ? "新建论文项目" : "New paper project"}</span>
          <button
            className="btn icon sm"
            onClick={onClose}
            disabled={busy}
            aria-label={locale === "zh-CN" ? "关闭新建项目" : "Close new project"}
          >
            ✕
          </button>
        </header>
        <div className="modal-body">
          <div className="row">
            <div className="field grow">
              <label htmlFor={`${formId}-title-input`}>{locale === "zh-CN" ? "标题 *" : "Title *"}</label>
              <input
                id={`${formId}-title-input`}
                autoFocus
                value={form.title}
                onChange={(event) => setForm({ ...form, title: event.target.value })}
                placeholder="Multi-agent LLM systems for automated survey writing"
              />
            </div>
            <div className="field grow">
              <label htmlFor={`${formId}-title-zh`}>{locale === "zh-CN" ? "中文标题" : "Chinese title"}</label>
              <input
                id={`${formId}-title-zh`}
                value={form.title_zh}
                onChange={(event) => setForm({ ...form, title_zh: event.target.value })}
                placeholder="多智能体大模型自动综述写作"
              />
            </div>
          </div>

          <div className="field">
            <label htmlFor={`${formId}-idea`}>
              {locale === "zh-CN" ? "研究想法 / 拟贡献" : "Research idea / intended contribution"}
            </label>
            <textarea
              id={`${formId}-idea`}
              rows={4}
              value={form.idea}
              onChange={(event) => setForm({ ...form, idea: event.target.value })}
              placeholder={
                locale === "zh-CN"
                  ? "描述你想做什么、和现有工作有什么不同。"
                  : "What you want to do, and how it differs from existing work."
              }
            />
            <span className="hint">
              {locale === "zh-CN"
                ? "这段文字会用于：按想法检索相关文献、在图谱中定位你的工作、以及 AI 规划论文贡献。写得具体一些收益最大。"
                : "Used for idea-based retrieval, positioning your work on the landscape, and the planner agent's contribution claim. Specificity pays off here."}
            </span>
          </div>

          <div className="row">
            <div className="field grow">
              <label htmlFor={`${formId}-field`}>{locale === "zh-CN" ? "研究领域" : "Research field"}</label>
              <input
                id={`${formId}-field`}
                value={form.research_field}
                onChange={(event) => setForm({ ...form, research_field: event.target.value })}
                placeholder="machine learning / NLP"
              />
            </div>
            <div className="field grow">
              <label htmlFor={`${formId}-venue`}>{locale === "zh-CN" ? "目标会议 / 期刊" : "Target venue"}</label>
              <input
                id={`${formId}-venue`}
                value={form.target_venue}
                onChange={(event) => setForm({ ...form, target_venue: event.target.value })}
                placeholder="ACL 2026"
              />
            </div>
          </div>

          <div className="row">
            <div className="field grow">
              <label htmlFor={`${formId}-template`}>{locale === "zh-CN" ? "论文类型模板" : "Structure template"}</label>
              <select
                id={`${formId}-template`}
                value={form.template_id}
                onChange={(event) => setForm({ ...form, template_id: event.target.value })}
              >
                {templates.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    [{templateCategory(entry.category, locale)}] {locale === "zh-CN" ? entry.name_zh : entry.name} · {entry.section_count}{" "}
                    {locale === "zh-CN" ? "节" : "sections"} · ~{entry.total_words} {locale === "zh-CN" ? "词" : "words"}
                  </option>
                ))}
              </select>
              {template && (
                <span className="hint">
                  {locale === "zh-CN" ? template.description_zh || template.description : template.description}
                  {" "}
                  {locale === "zh-CN"
                    ? "这是结构指导，不替代投稿 venue 的官方排版文件。"
                    : "This is structure guidance, not the venue's official class file."}
                </span>
              )}
            </div>
            <div className="field" style={{ width: 150 }}>
              <label htmlFor={`${formId}-target-words`}>{locale === "zh-CN" ? "目标字数" : "Target words"}</label>
              <input
                id={`${formId}-target-words`}
                type="number"
                value={form.target_words || ""}
                placeholder={String(template?.total_words ?? 6000)}
                onChange={(event) =>
                  setForm({ ...form, target_words: Number(event.target.value) || 0 })
                }
              />
            </div>
          </div>

          <div className="row">
            <div className="field grow">
              <label htmlFor={`${formId}-language`}>{locale === "zh-CN" ? "写作语言" : "Writing language"}</label>
              <select
                id={`${formId}-language`}
                value={form.language}
                onChange={(event) => setForm({ ...form, language: event.target.value })}
              >
                <option value="en">English</option>
                <option value="zh">中文</option>
              </select>
            </div>
            <div className="field grow">
              <label htmlFor={`${formId}-citation-style`}>{locale === "zh-CN" ? "引用格式" : "Citation style"}</label>
              <select
                id={`${formId}-citation-style`}
                value={form.citation_style}
                onChange={(event) => setForm({ ...form, citation_style: event.target.value })}
              >
                <option value="ieee">IEEE</option>
                <option value="acm">ACM</option>
                <option value="apa">APA</option>
                <option value="nature">Nature</option>
              </select>
            </div>
          </div>

          <div className="row wrap">
            <label className="row" style={{ gap: 6 }}>
              <input
                type="checkbox"
                checked={form.bilingual}
                onChange={(event) => setForm({ ...form, bilingual: event.target.checked })}
              />
              {locale === "zh-CN" ? "中英对照" : "Bilingual (paired translation)"}
            </label>
            <label className="row" style={{ gap: 6 }}>
              <input
                type="checkbox"
                checked={form.git_enabled}
                onChange={(event) => setForm({ ...form, git_enabled: event.target.checked })}
              />
              {locale === "zh-CN"
                ? "启用项目本地 Git（不自动推送）"
                : "Enable local project Git (never auto-push)"}
            </label>
          </div>

          {template && (
            <div className="card" style={{ marginTop: 8 }}>
              <h3>{locale === "zh-CN" ? "将创建的章节" : "Sections that will be created"}</h3>
              <div className="row wrap" style={{ gap: 5 }}>
                {template.sections.map((section) => (
                  <span key={section.key} className="chip" title={`${section.target_words} words`}>
                    {locale === "zh-CN" ? section.title_zh : section.title}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
        <footer>
          <button className="btn" onClick={onClose} disabled={busy}>
            {locale === "zh-CN" ? "取消" : "Cancel"}
          </button>
          <button className="btn primary" onClick={() => void submit()} disabled={busy}>
            {busy
              ? locale === "zh-CN" ? "创建中…" : "Creating…"
              : locale === "zh-CN" ? "创建" : "Create"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function templateCategory(category: string | undefined, locale: "zh-CN" | "en-US") {
  const id = category || "general";
  if (locale !== "zh-CN") return id.replace(/-/g, " ");
  return ({
    general: "通用",
    "academic-journal": "学术期刊",
    conference: "会议",
    "poster-presentation": "海报/展示",
    book: "书籍",
  } as Record<string, string>)[id] || id;
}

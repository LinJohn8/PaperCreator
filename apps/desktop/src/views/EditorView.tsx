/**
 * Manuscript editor.
 *
 * Three things this view is built around:
 *
 * * **The brief is visible while writing.** Each section carries the guidance the
 *   agent was given; showing it above the text is what lets a human and an agent
 *   work to the same specification.
 * * **Bilingual is side by side, not a toggle.** The requirement is a Chinese /
 *   English pair, and drift between them is the failure mode - two panes make
 *   drift visible immediately.
 * * **Streaming agent output lands in place.** While a writer agent runs, its
 *   tokens appear in the pane rather than in a log, so the user watches the
 *   section being written.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import * as endpoints from "../api/endpoints";
import { JobFailureError, waitForJob } from "../api/events";
import { CodeEditor } from "../components/CodeEditor";
import { useStore } from "../state/store";
import type { ManuscriptSyncStatus, PaperTemplate, Section, TranslationJobResult } from "../api/types";

export function EditorView() {
  const document = useStore((s) => s.document);
  const project = useStore((s) => s.project);
  const stats = useStore((s) => s.stats);
  const activeKey = useStore((s) => s.activeSectionKey);
  const setActiveSection = useStore((s) => s.setActiveSection);
  const dirty = useStore((s) => s.dirtySections);
  const editSection = useStore((s) => s.editSection);
  const saveSection = useStore((s) => s.saveSection);
  const streaming = useStore((s) => s.streaming);
  const locale = useStore((s) => s.locale);
  const startAgentRun = useStore((s) => s.startAgentRun);
  const reloadDocument = useStore((s) => s.reloadDocument);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const hasLlm = useStore((s) => s.health?.llm.has_any ?? false);

  const [showPaired, setShowPaired] = useState(false);
  const [templating, setTemplating] = useState(false);
  const [sectionDialog, setSectionDialog] = useState<"add" | "edit" | "">("");
  const [pairedDraft, setPairedDraft] = useState("");
  const [selectedText, setSelectedText] = useState("");
  const [translationOpen, setTranslationOpen] = useState(false);
  const [importPreview, setImportPreview] = useState<Record<string, any> | null>(null);
  const [articleBusy, setArticleBusy] = useState("");

  const section = useMemo(
    () => document?.sections.find((entry) => entry.key === activeKey) ?? null,
    [document, activeKey],
  );

  useEffect(() => {
    if (!activeKey && document?.sections.length) {
      setActiveSection(document.sections[0].key);
    }
  }, [activeKey, document, setActiveSection]);

  useEffect(() => {
    if (project?.bilingual) setShowPaired(true);
  }, [project?.bilingual]);

  useEffect(() => {
    setPairedDraft(section?.content_zh ?? "");
  }, [section?.key, section?.content_zh]);

  async function moveSection(direction: -1 | 1) {
    if (!project || !document || !section) return;
    const keys = document.sections.map((entry) => entry.key);
    const index = keys.indexOf(section.key);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= keys.length) return;
    [keys[index], keys[target]] = [keys[target], keys[index]];
    try {
      await endpoints.writing.reorder(project.id, keys);
      await reloadDocument();
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "调整章节顺序" : "reordering sections");
    }
  }

  async function savePaired() {
    if (!project || !section || pairedDraft === section.content_zh) return;
    try {
      await endpoints.writing.updateSection(project.id, section.key, { content_zh: pairedDraft });
      await reloadDocument();
      notify({ kind: "success", message: locale === "zh-CN" ? "对照文本已保存" : "Paired text saved" });
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "保存对照文本" : "saving paired text");
    }
  }

  if (!document?.sections.length) {
    return (
      <div className="view">
        <h1>{locale === "zh-CN" ? "手稿" : "Manuscript"}</h1>
        <p className="sub">
          {locale === "zh-CN"
            ? "还没有章节结构。选择一个模板开始。"
            : "No section structure yet. Start from a template."}
        </p>
        {project && (
          <ManuscriptSyncBanner
            projectId={project.id}
            revision={document?.updated_at ?? ""}
          />
        )}
        {project && (
          <ManuscriptActions
            projectId={project.id}
            projectPath={project.path}
            busy={articleBusy}
            onBusy={setArticleBusy}
            onImportPreview={setImportPreview}
          />
        )}
        {importPreview && project && (
          <ManuscriptImportDialog
            key={`${String(importPreview.source_sha256)}-${String(importPreview.method)}`}
            projectId={project.id}
            preview={importPreview}
            onPreviewChange={setImportPreview}
            onClose={() => setImportPreview(null)}
            onDone={async () => {
              setImportPreview(null);
              await reloadDocument();
            }}
          />
        )}
        <div className="row wrap" style={{ marginBottom: 14 }}>
          <button className="btn primary" onClick={() => setSectionDialog("add")}>
            ＋ {locale === "zh-CN" ? "新增空白章节" : "Add blank section"}
          </button>
          <span className="dim">
            {locale === "zh-CN" ? "或者从下方结构模板开始" : "or start from a structure template below"}
          </span>
        </div>
        <TemplatePicker onDone={() => setTemplating(false)} />
        {sectionDialog === "add" && project && (
          <SectionDialog
            mode="add"
            projectId={project.id}
            section={null}
            onClose={() => setSectionDialog("")}
            onDone={async (key) => {
              await reloadDocument();
              setSectionDialog("");
              if (key) setActiveSection(key);
            }}
          />
        )}
      </div>
    );
  }

  const liveText = streaming[activeKey];
  const currentValue =
    dirty[activeKey] !== undefined ? dirty[activeKey] : section?.content ?? "";

  return (
    <div className="editor-wrap">
      <div className="editor-tabs">
        {document.sections.map((entry) => (
          <div
            key={entry.key}
            className={`editor-tab${entry.key === activeKey ? " active" : ""}`}
            onClick={() => setActiveSection(entry.key)}
            title={entry.guidance || entry.title}
          >
            <span>{locale === "zh-CN" && entry.title_zh ? entry.title_zh : entry.title}</span>
            {dirty[entry.key] !== undefined && <span className="dirty" />}
          </div>
        ))}
        <div className="grow" />
        <button
          className="btn sm"
          onClick={() => setTemplating(true)}
          title={locale === "zh-CN" ? "应用/更换模板" : "Apply a template"}
        >
          ＋ {locale === "zh-CN" ? "结构" : "structure"}
        </button>
        <button className="btn sm" onClick={() => setSectionDialog("add")}>
          ＋ {locale === "zh-CN" ? "章节" : "section"}
        </button>
        {section && (
          <>
            <button className="btn sm" onClick={() => setSectionDialog("edit")}>
              {locale === "zh-CN" ? "章节设置" : "Section settings"}
            </button>
            <button className="btn icon sm" onClick={() => void moveSection(-1)} title={locale === "zh-CN" ? "上移" : "Move up"}>↑</button>
            <button className="btn icon sm" onClick={() => void moveSection(1)} title={locale === "zh-CN" ? "下移" : "Move down"}>↓</button>
          </>
        )}
      </div>

      {project && (
        <ManuscriptSyncBanner
          projectId={project.id}
          revision={document.updated_at}
        />
      )}

      {project && (
        <ManuscriptActions
          projectId={project.id}
          projectPath={project.path}
          busy={articleBusy}
          onBusy={setArticleBusy}
          onImportPreview={setImportPreview}
        />
      )}

      {importPreview && project && (
        <ManuscriptImportDialog
          key={`${String(importPreview.source_sha256)}-${String(importPreview.method)}`}
          projectId={project.id}
          preview={importPreview}
          onPreviewChange={setImportPreview}
          onClose={() => setImportPreview(null)}
          onDone={async () => {
            setImportPreview(null);
            await reloadDocument();
          }}
        />
      )}

      {templating && (
        <div className="modal-backdrop" onClick={() => setTemplating(false)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <header>
              <span>{locale === "zh-CN" ? "章节结构" : "Section structure"}</span>
              <button className="btn icon sm" onClick={() => setTemplating(false)} aria-label={locale === "zh-CN" ? "关闭章节结构" : "Close section structure"}>
                ✕
              </button>
            </header>
            <div className="modal-body">
              <TemplatePicker onDone={() => setTemplating(false)} />
            </div>
          </div>
        </div>
      )}

      {sectionDialog && project && (
        <SectionDialog
          mode={sectionDialog}
          projectId={project.id}
          section={sectionDialog === "edit" ? section : null}
          onClose={() => setSectionDialog("")}
          onDone={async (key) => {
            await reloadDocument();
            setSectionDialog("");
            if (key) setActiveSection(key);
          }}
        />
      )}

      {translationOpen && project && section && (
        <TranslationDialog
          text={selectedText.trim() || currentValue}
          selectionOnly={Boolean(selectedText.trim())}
          source={project.language === "zh" ? "zh" : "en"}
          target={project.language === "zh" ? "en" : "zh-CN"}
          onClose={() => setTranslationOpen(false)}
          onApply={(translated) => {
            setPairedDraft(translated);
            setShowPaired(true);
            setTranslationOpen(false);
          }}
        />
      )}

      {section && (
        <>
          {section.guidance && (
            <div className="guidance">
              <strong>{locale === "zh-CN" ? "写作要求：" : "Brief: "}</strong>
              {section.guidance}
              {section.target_words > 0 && (
                <span className="dim">
                  {" "}
                  · {locale === "zh-CN" ? "目标" : "target"} {section.target_words}{" "}
                  {locale === "zh-CN" ? "词" : "words"} ({section.word_count}{" "}
                  {locale === "zh-CN" ? "当前" : "now"})
                </span>
              )}
            </div>
          )}

          <div className="row" style={{ padding: "6px 12px", flex: "none" }}>
            <span className="chip">{sectionStatusLabel(section.status, locale)}</span>
            <span className="dim">
              {section.word_count} {locale === "zh-CN" ? "词" : "words"}
              {section.cited_paper_ids.length > 0 &&
                ` · ${section.cited_paper_ids.length} ${locale === "zh-CN" ? "处引用" : "citations"}`}
            </span>
            <div className="grow" />
            {liveText && (
              <span className="chip on">
                <span className="spin">◌</span>{" "}
                {locale === "zh-CN" ? "AI 正在写入" : "agent writing"}
              </span>
            )}
            {project?.bilingual && (
              <label className="row" style={{ gap: 5 }}>
                <input
                  type="checkbox"
                  checked={showPaired}
                  onChange={(event) => setShowPaired(event.target.checked)}
                />
                {locale === "zh-CN" ? "对照" : "paired"}
              </label>
            )}
            <select
              value={section.status}
              onChange={(event) =>
                void endpoints.writing
                  .updateSection(project!.id, section.key, { status: event.target.value })
                  .then(() => useStore.getState().reloadDocument())
              }
              style={{ padding: "1px 6px", fontSize: "var(--fs-xs)" }}
            >
              {["empty", "drafting", "drafted", "reviewed", "final"].map((value) => (
                <option key={value} value={value}>
                  {sectionStatusLabel(value, locale)}
                </option>
              ))}
            </select>
            <button
              className="btn sm"
              disabled={!hasLlm}
              title={
                hasLlm
                  ? locale === "zh-CN"
                    ? "让 AI 撰写本章节"
                    : "Have the agent draft this section"
                  : "Configure an LLM provider in Settings first"
              }
              onClick={() =>
                void startAgentRun({ pipeline: "section", section_keys: [section.key] })
              }
            >
              {locale === "zh-CN" ? "AI 撰写本节" : "Draft with AI"}
            </button>
            <button
              className="btn sm"
              disabled={!currentValue.trim()}
              onClick={() => setTranslationOpen(true)}
              title={
                selectedText
                  ? locale === "zh-CN" ? "翻译已选择的专业词汇或文本" : "Translate the selected term or text"
                  : locale === "zh-CN" ? "翻译整个章节" : "Translate the whole section"
              }
            >
              {selectedText
                ? locale === "zh-CN" ? "术语翻译" : "Translate selection"
                : locale === "zh-CN" ? "翻译本节" : "Translate section"}
            </button>
            <button
              className="btn sm primary"
              disabled={dirty[section.key] === undefined}
              onClick={() => void saveSection(section.key)}
            >
              {locale === "zh-CN" ? "保存" : "Save"} <kbd className="dim">⌘S</kbd>
            </button>
          </div>

          <div className="editor-panes">
            <div className="editor-pane">
              <div className="pane-label">
                {project?.language === "zh"
                  ? locale === "zh-CN" ? "中文（主）" : "Chinese (primary)"
                  : locale === "zh-CN" ? "英文（主）" : "English (primary)"}
                {liveText ? ` · ${locale === "zh-CN" ? "实时" : "live"}` : ""}
              </div>
              {liveText ? (
                <div
                  className="panel-body"
                  style={{ flex: 1, whiteSpace: "pre-wrap", fontFamily: "var(--font-mono)" }}
                >
                  {liveText}
                </div>
              ) : (
                <CodeEditor
                  value={currentValue}
                  onChange={(next) => editSection(section.key, next)}
                  onSelectionChange={setSelectedText}
                  onSave={() => void saveSection(section.key)}
                  placeholder={
                    section.guidance ||
                    (locale === "zh-CN" ? "开始写作…" : "Start writing…")
                  }
                />
              )}
            </div>

            {showPaired && (
              <div className="editor-pane">
                <div className="pane-label">
                  {project?.language === "zh"
                    ? locale === "zh-CN" ? "英文（对照）" : "English (paired)"
                    : locale === "zh-CN" ? "中文（对照）" : "Chinese (paired)"}
                  {section.content_zh ? "" : ` · ${locale === "zh-CN" ? "未翻译" : "untranslated"}`}
                  <span className="grow" />
                  {pairedDraft !== section.content_zh && (
                    <button className="btn sm primary" onClick={() => void savePaired()}>
                      {locale === "zh-CN" ? "保存对照" : "Save paired"}
                    </button>
                  )}
                </div>
                <CodeEditor
                  value={pairedDraft}
                  onChange={setPairedDraft}
                  onSave={() => void savePaired()}
                  placeholder={
                    locale === "zh-CN"
                      ? "对照版本。可以由 AI 翻译生成。"
                      : "The paired version. The translator agent can produce it."
                  }
                />
              </div>
            )}
          </div>
        </>
      )}

      {stats && <BilingualBar />}
    </div>
  );
}

function ManuscriptActions({
  projectId,
  projectPath,
  busy,
  onBusy,
  onImportPreview,
}: {
  projectId: string;
  projectPath: string;
  busy: string;
  onBusy: (value: string) => void;
  onImportPreview: (preview: Record<string, any>) => void;
}) {
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const setView = useStore((s) => s.setView);
  const [venuePreview, setVenuePreview] = useState<Record<string, any> | null>(null);

  async function importFile() {
    const path = await window.papercreator?.dialog.openFile([
      { name: "Manuscripts", extensions: ["pdf", "docx", "md", "markdown", "txt", "tex"] },
      { name: "All files", extensions: ["*"] },
    ]);
    if (!path) return;
    onBusy("import");
    try {
      onImportPreview(await endpoints.writing.previewImport(path, projectId));
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "预览手稿导入" : "previewing manuscript import");
    } finally {
      onBusy("");
    }
  }

  async function exportFormat(format: "markdown" | "docx" | "latex") {
    onBusy(format);
    try {
      const result = await endpoints.exports.run(projectId, {
        format,
        language: "primary",
        cited_only: true,
        document_class: "article",
      });
      notify({
        kind: (result.warnings as string[])?.length ? "warning" : "success",
        message:
          locale === "zh-CN"
            ? `${format.toUpperCase()} 已导出`
            : `${format.toUpperCase()} exported`,
        detail: (result.warnings as string[])?.join(" · ") || String(result.path),
      });
      if (result.path) void window.papercreator?.shell.showItem(String(result.path));
    } catch (error) {
      reportError(error, locale === "zh-CN" ? `导出 ${format}` : `exporting ${format}`);
    } finally {
      onBusy("");
    }
  }

  async function overleafArchive() {
    onBusy("overleaf");
    try {
      const result = await endpoints.exports.overleafZip(projectId, "article");
      notify({
        kind: "success",
        message: locale === "zh-CN" ? "Overleaf 上传包已生成" : "Overleaf upload archive created",
        detail: String(result.path),
      });
      if (result.path) void window.papercreator?.shell.showItem(String(result.path));
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "生成 Overleaf 上传包" : "building an Overleaf archive");
    } finally {
      onBusy("");
    }
  }

  async function chooseVenueTemplate() {
    const path = await window.papercreator?.dialog.openFile([
      { name: "LaTeX template ZIP", extensions: ["zip"] },
    ]);
    if (!path) return;
    onBusy("venue-template");
    try {
      setVenuePreview(await endpoints.writing.previewVenueTemplate(path));
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "检查投稿模板包" : "inspecting the venue template package");
    } finally {
      onBusy("");
    }
  }

  return (
    <div className="manuscript-actions">
      <strong>{locale === "zh-CN" ? "文章" : "Article"}</strong>
      <button className="btn sm" disabled={Boolean(busy)} onClick={() => void importFile()}>
        {busy === "import" ? (locale === "zh-CN" ? "解析中…" : "Reading…") : (locale === "zh-CN" ? "导入…" : "Import…")}
      </button>
      <span className="manuscript-action-separator" />
      {(["markdown", "docx", "latex"] as const).map((format) => (
        <button key={format} className="btn sm" disabled={Boolean(busy)} onClick={() => void exportFormat(format)}>
          {busy === format ? "…" : format === "markdown" ? "Markdown" : format === "docx" ? "Word" : "LaTeX"}
        </button>
      ))}
      <button className="btn sm" disabled={Boolean(busy)} onClick={() => void overleafArchive()}>
        {busy === "overleaf" ? "…" : "Overleaf ZIP"}
      </button>
      <button className="btn sm" disabled={Boolean(busy)} onClick={() => void chooseVenueTemplate()}>
        {busy === "venue-template" ? "…" : (locale === "zh-CN" ? "导入投稿模板包…" : "Import venue template…")}
      </button>
      <button className="btn sm" onClick={() => setView("export")}>
        {locale === "zh-CN" ? "全部导出与同步…" : "All export and sync…"}
      </button>
      <div className="grow" />
      <button className="btn sm" onClick={() => void window.papercreator?.shell.openPath(projectPath)}>
        {locale === "zh-CN" ? "打开项目文件夹" : "Open project folder"}
      </button>
      {venuePreview && (
        <VenueTemplateDialog
          projectId={projectId}
          preview={venuePreview}
          onClose={() => setVenuePreview(null)}
          onDone={(path) => {
            setVenuePreview(null);
            void window.papercreator?.shell.openPath(path);
          }}
        />
      )}
    </div>
  );
}

function VenueTemplateDialog({
  projectId,
  preview,
  onClose,
  onDone,
}: {
  projectId: string;
  preview: Record<string, any>;
  onClose: () => void;
  onDone: (path: string) => void;
}) {
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const latex = preview.latex as Record<string, number>;
  const [form, setForm] = useState({
    name: String(preview.source_name || "").replace(/\.zip$/i, ""),
    source_url: "",
    license_name: "",
    confirm_license: false,
  });
  const [busy, setBusy] = useState(false);

  async function apply() {
    setBusy(true);
    try {
      const result = await endpoints.writing.importVenueTemplate(projectId, {
        source_path: preview.source_path,
        source_sha256: preview.source_sha256,
        ...form,
      });
      notify({
        kind: (result.warnings as string[])?.length ? "warning" : "success",
        message: locale === "zh-CN" ? "投稿排版模板已导入" : "Venue layout template imported",
        detail: (result.warnings as string[])?.join(" · ") || String(result.template.path),
      });
      onDone(String(result.template.path));
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "导入投稿排版模板" : "importing the venue layout template");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <header>
          <span>{locale === "zh-CN" ? "导入官方投稿排版包" : "Import official venue layout package"}</span>
          <button className="btn icon sm" onClick={onClose} aria-label={locale === "zh-CN" ? "关闭投稿排版包导入" : "Close venue layout import"}>×</button>
        </header>
        <div className="modal-body">
          <p className="sub">
            {locale === "zh-CN"
              ? "SCI/SSCI 是索引体系，不是统一排版格式。这里导入你从期刊、会议或 Overleaf Gallery 获得的具体 LaTeX ZIP；内置结构模板仍负责论文内容组织。"
              : "SCI/SSCI are indexes, not layout formats. Import the exact LaTeX ZIP obtained from a journal, conference or Overleaf Gallery; built-in templates continue to control content structure."}
          </p>
          <div className="row wrap">
            <span className="chip">{preview.file_count} files</span>
            <span className="chip">{latex.tex_files} .tex</span>
            <span className="chip">{latex.class_files} .cls</span>
            <span className="chip">{latex.style_files} .sty</span>
            <span className="chip mono">SHA-256 {String(preview.source_sha256).slice(0, 16)}…</span>
          </div>
          {(preview.warnings as string[]).map((warning) => <p className="warn-text" key={warning}>⚠ {warning}</p>)}
          <div className="field"><label>{locale === "zh-CN" ? "模板名称" : "Template name"}</label><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></div>
          <div className="field"><label>{locale === "zh-CN" ? "来源页面（建议填写）" : "Source page (recommended)"}</label><input value={form.source_url} onChange={(event) => setForm({ ...form, source_url: event.target.value })} placeholder="https://…" /></div>
          <div className="field"><label>{locale === "zh-CN" ? "许可证或使用条款" : "Licence or terms"}</label><input value={form.license_name} onChange={(event) => setForm({ ...form, license_name: event.target.value })} placeholder={locale === "zh-CN" ? "例如：LPPL 1.3c / publisher submission use" : "e.g. LPPL 1.3c / publisher submission use"} /></div>
          {(preview.license_candidates as string[]).length > 0 && <p className="dim">{locale === "zh-CN" ? "包内可能的许可证文件：" : "Possible licence files in archive: "}{(preview.license_candidates as string[]).join(", ")}</p>}
          <label className="row" style={{ gap: 7 }}><input type="checkbox" checked={form.confirm_license} onChange={(event) => setForm({ ...form, confirm_license: event.target.checked })} />{locale === "zh-CN" ? "我确认有权在本项目中使用并保存该模板包。" : "I confirm I may use and store this template package in this project."}</label>
          <p className="dim">{locale === "zh-CN" ? "导入仅解压并登记，不会执行或编译包内代码。ZIP 中的路径穿越、符号链接和异常膨胀会被拒绝。" : "Import only extracts and records files; it does not execute or compile them. Path traversal, symlinks and suspicious expansion are rejected."}</p>
        </div>
        <footer><button className="btn" onClick={onClose}>{locale === "zh-CN" ? "取消" : "Cancel"}</button><button className="btn primary" disabled={busy || !form.name.trim() || !form.confirm_license} onClick={() => void apply()}>{busy ? (locale === "zh-CN" ? "导入中…" : "Importing…") : (locale === "zh-CN" ? "确认导入" : "Confirm import")}</button></footer>
      </div>
    </div>
  );
}

function ManuscriptImportDialog({
  projectId,
  preview,
  onPreviewChange,
  onClose,
  onDone,
}: {
  projectId: string;
  preview: Record<string, any>;
  onPreviewChange: (preview: Record<string, any>) => void;
  onClose: () => void;
  onDone: () => Promise<void>;
}) {
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const sections = (preview.sections ?? []) as Array<Record<string, any>>;
  const [selected, setSelected] = useState<number[]>(sections.map((item) => Number(item.index)));
  const [mode, setMode] = useState<"append" | "replace">("append");
  const [busy, setBusy] = useState(false);
  const ocrCapabilities = preview.ocr_capabilities as Record<string, any> | undefined;
  const ocrLanguages = (ocrCapabilities?.languages ?? []) as string[];
  const languageOptions = useMemo(() => {
    const options = [...ocrLanguages];
    for (const language of ["chi_sim", "chi_tra"]) {
      if (ocrLanguages.includes(language) && ocrLanguages.includes("eng")) {
        options.unshift(`${language}+eng`);
      }
    }
    return [...new Set(options)];
  }, [ocrLanguages]);
  const [ocrLanguage, setOcrLanguage] = useState(
    String(preview.ocr_languages || ocrCapabilities?.default_languages || "eng"),
  );
  const [ocrMaxPages, setOcrMaxPages] = useState(
    Number(preview.ocr_max_pages || ocrCapabilities?.default_max_pages || 50),
  );

  async function runOcrPreview() {
    setBusy(true);
    try {
      const next = await endpoints.writing.previewImport(
        String(preview.source_path),
        projectId,
        {
          use_ocr: true,
          ocr_languages: ocrLanguage,
          ocr_max_pages: Math.max(1, Math.min(200, Math.round(ocrMaxPages))),
        },
      );
      onPreviewChange(next);
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "运行本地 OCR 预览" : "running local OCR preview");
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!selected.length) return;
    if (mode === "replace") {
      const ok = window.confirm(
        locale === "zh-CN"
          ? "用所选内容替换当前全部章节？操作前会自动创建可恢复快照，原始导入文件也会保留在项目内部。"
          : "Replace every current section with the selected content? A recovery snapshot and managed source copy will be created first.",
      );
      if (!ok) return;
    }
    setBusy(true);
    try {
      const result = await endpoints.writing.applyImport(projectId, {
        source_path: preview.source_path,
        source_sha256: preview.source_sha256,
        mode,
        selected_indices: selected,
        confirm_replace: mode === "replace",
        use_ocr: Boolean(preview.ocr_used),
        ocr_languages: String(preview.ocr_languages || "eng"),
        ocr_max_pages: Number(preview.ocr_max_pages || 50),
      });
      notify({
        kind: (result.warnings as string[])?.length ? "warning" : "success",
        message:
          locale === "zh-CN"
            ? `已导入 ${result.created_count} 个章节`
            : `Imported ${result.created_count} section(s)`,
        detail: (result.warnings as string[])?.join(" · ") || String(result.managed_source),
      });
      await onDone();
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "导入手稿" : "importing the manuscript");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(event) => event.stopPropagation()}>
        <header>
          <span>{locale === "zh-CN" ? "预览手稿导入" : "Preview manuscript import"}</span>
          <button className="btn icon sm" onClick={onClose} aria-label={locale === "zh-CN" ? "关闭手稿导入预览" : "Close manuscript import preview"}>×</button>
        </header>
        <div className="modal-body">
          <div className="row wrap" style={{ marginBottom: 12 }}>
            <strong>{String(preview.source_name)}</strong>
            <span className="chip">{String(preview.method)}</span>
            {preview.page_count > 0 && <span className="chip">{preview.page_count} pages</span>}
            <span className="chip">{Number(preview.characters).toLocaleString()} chars</span>
            <span className="dim mono truncate">SHA-256 {String(preview.source_sha256).slice(0, 16)}…</span>
          </div>
          {(preview.warnings as string[]).map((warning) => <p className="warn-text" key={warning}>⚠ {warning}</p>)}
          {preview.requires_ocr && ocrCapabilities && (
            <div className="ocr-preview-panel">
              <strong>{locale === "zh-CN" ? "此 PDF 没有可用文字层" : "This PDF has no usable text layer"}</strong>
              {ocrCapabilities.available ? (
                <>
                  <div className="row wrap">
                    <div className="field grow">
                      <label htmlFor="ocr-language">{locale === "zh-CN" ? "OCR 语言" : "OCR language"}</label>
                      <select id="ocr-language" value={ocrLanguage} disabled={busy} onChange={(event) => setOcrLanguage(event.target.value)}>
                        {languageOptions.map((language) => <option value={language} key={language}>{language}</option>)}
                      </select>
                    </div>
                    <div className="field">
                      <label htmlFor="ocr-page-limit">{locale === "zh-CN" ? "最多页数" : "Maximum pages"}</label>
                      <input
                        id="ocr-page-limit"
                        type="number"
                        min={1}
                        max={200}
                        value={ocrMaxPages}
                        disabled={busy}
                        onChange={(event) => setOcrMaxPages(Number(event.target.value))}
                      />
                    </div>
                  </div>
                  <button
                    className="btn primary"
                    disabled={busy || !ocrLanguage || !Number.isFinite(ocrMaxPages) || ocrMaxPages < 1 || ocrMaxPages > 200}
                    onClick={() => void runOcrPreview()}
                  >
                    {busy ? (locale === "zh-CN" ? "OCR 处理中…" : "Running OCR…") : (locale === "zh-CN" ? "运行本地 OCR 并重新预览" : "Run local OCR and refresh preview")}
                  </button>
                  <p className="dim">
                    {locale === "zh-CN"
                      ? `全程离线；使用 ${String(ocrCapabilities.engine)} + ${String((ocrCapabilities.renderers ?? [])[0] || "renderer")}，每页最多 ${Number(ocrCapabilities.page_timeout_seconds)} 秒。`
                      : `Fully offline; uses ${String(ocrCapabilities.engine)} + ${String((ocrCapabilities.renderers ?? [])[0] || "renderer")}, with ${Number(ocrCapabilities.page_timeout_seconds)} seconds per page.`}
                  </p>
                </>
              ) : (
                <div className="callout warning">
                  <div>{locale === "zh-CN" ? "本机 OCR 尚不可用。安装 Tesseract 及所需语言包，并安装 PaperCreator OCR extra（PDFium）或提供 pdftoppm 后重试。" : "Local OCR is unavailable. Install Tesseract with the needed language packs, plus the PaperCreator OCR extra (PDFium) or pdftoppm, then retry."}</div>
                  {(ocrCapabilities.diagnostics as string[] ?? []).map((item) => <div className="dim" key={item}>{item}</div>)}
                </div>
              )}
            </div>
          )}
          {preview.ocr_used && (
            <div className="callout success">
              {locale === "zh-CN"
                ? `已使用本地 OCR 生成此预览（${String(preview.ocr_languages)}，最多 ${Number(preview.ocr_max_pages)} 页）。原 PDF 未修改。`
                : `This preview uses local OCR (${String(preview.ocr_languages)}, up to ${Number(preview.ocr_max_pages)} pages). The original PDF was not modified.`}
            </div>
          )}
          <div className="card">
            <label className="row" style={{ gap: 7 }}><input type="radio" checked={mode === "append"} onChange={() => setMode("append")} />{locale === "zh-CN" ? "追加到现有章节（名称冲突时自动生成新 key）" : "Append to existing sections (conflicting keys are renamed)"}</label>
            <label className="row" style={{ gap: 7, marginTop: 8 }}><input type="radio" checked={mode === "replace"} onChange={() => setMode("replace")} />{locale === "zh-CN" ? "替换当前手稿（先创建恢复快照）" : "Replace current manuscript (create a recovery snapshot first)"}</label>
          </div>
          <div className="import-section-list">
            {sections.map((section) => {
              const index = Number(section.index);
              return (
                <label className="import-section-item" key={index}>
                  <input type="checkbox" checked={selected.includes(index)} onChange={() => setSelected((current) => current.includes(index) ? current.filter((value) => value !== index) : [...current, index])} />
                  <div className="grow">
                    <div className="row"><strong>{String(section.title)}</strong><span className="chip mono">{String(section.key)}</span>{section.key_conflict && <span className="chip warn">{locale === "zh-CN" ? "key 冲突" : "key conflict"}</span>}<span className="dim grow" /><span className="dim">{section.word_count} {locale === "zh-CN" ? "词" : "words"}</span></div>
                    <div className="import-preview-text">{String(section.content_preview || "")}</div>
                  </div>
                </label>
              );
            })}
          </div>
        </div>
        <footer>
          <span className="dim grow">{locale === "zh-CN" ? "导入源会复制到项目 .papercreator/imports 作为审计副本。" : "The source is copied to project .papercreator/imports as an audit copy."}</span>
          <button className="btn" onClick={onClose} disabled={busy}>{locale === "zh-CN" ? "取消" : "Cancel"}</button>
          <button className="btn primary" onClick={() => void apply()} disabled={busy || Boolean(preview.requires_ocr) || selected.length === 0}>{busy ? (locale === "zh-CN" ? "导入中…" : "Importing…") : (locale === "zh-CN" ? `导入所选 ${selected.length} 节` : `Import ${selected.length} selected`)}</button>
        </footer>
      </div>
    </div>
  );
}

function TranslationDialog({
  text,
  selectionOnly,
  source,
  target,
  onClose,
  onApply,
}: {
  text: string;
  selectionOnly: boolean;
  source: "en" | "zh";
  target: "en" | "zh-CN";
  onClose: () => void;
  onApply: (text: string) => void;
}) {
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const [providers, setProviders] = useState<Array<Record<string, any>>>([]);
  const [provider, setProvider] = useState(selectionOnly ? "builtin-glossary" : "mymemory");
  const [result, setResult] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [externalConfirmed, setExternalConfirmed] = useState(false);
  const [jobId, setJobId] = useState("");
  const [progress, setProgress] = useState(0);
  const [progressMessage, setProgressMessage] = useState("");
  const [cancelling, setCancelling] = useState(false);

  useEffect(() => {
    void endpoints.writing.translationProviders().then(({ items }) => {
      setProviders(items);
      if (!selectionOnly && items.some((entry) => entry.id === "llm" && entry.available)) {
        setProvider("llm");
      }
    });
  }, [selectionOnly]);

  async function translate() {
    setBusy(true);
    setResult("");
    setNote("");
    try {
      let response: { text?: string; note?: string; found?: boolean };
      if (provider === "mymemory" && text.length > 10_000) {
        const accepted = await endpoints.writing.startTranslationJob({
          text,
          source,
          target,
          provider,
          confirm_external: externalConfirmed,
        });
        setJobId(accepted.job_id);
        setProgressMessage(locale === "zh-CN" ? "长文翻译已进入队列" : "Long translation queued");
        response = (await waitForJob(accepted.job_id, (fraction, message) => {
          setProgress(fraction);
          setProgressMessage(message);
        })) as unknown as TranslationJobResult;
      } else {
        response = await endpoints.writing.translate({
          text,
          source,
          target,
          provider,
          confirm_external: provider === "mymemory" ? externalConfirmed : false,
        });
      }
      setResult(response.text || "");
      setNote(response.note || "");
      if (response.found === false) {
        notify({
          kind: "warning",
          message:
            locale === "zh-CN"
              ? "内置术语表没有精确匹配；可改用 MyMemory 或已配置的大模型"
              : "No exact glossary match; try MyMemory or a configured LLM",
        });
      }
    } catch (error) {
      if (error instanceof JobFailureError && error.failure.cancelled) {
        notify({ kind: "warning", message: locale === "zh-CN" ? "翻译已取消，原文未改动" : "Translation cancelled; source text was unchanged" });
      } else {
        reportError(error, locale === "zh-CN" ? "翻译文本" : "translating text");
      }
    } finally {
      setBusy(false);
      setJobId("");
      setCancelling(false);
    }
  }

  async function copy() {
    if (!result) return;
    await navigator.clipboard.writeText(result);
    notify({ kind: "success", message: locale === "zh-CN" ? "译文已复制" : "Translation copied" });
  }

  const selectedProvider = providers.find((entry) => entry.id === provider);
  return (
    <div className="modal-backdrop" onClick={() => !busy && onClose()}>
      <div className="modal wide" onClick={(event) => event.stopPropagation()}>
        <header>
          <span>{selectionOnly ? (locale === "zh-CN" ? "专业术语 / 选中文本翻译" : "Term / selection translation") : (locale === "zh-CN" ? "章节翻译" : "Section translation")}</span>
          <button className="btn icon sm" disabled={busy} onClick={onClose} aria-label={locale === "zh-CN" ? "关闭翻译" : "Close translation"}>✕</button>
        </header>
        <div className="modal-body">
          <div className="row">
            <div className="field grow">
              <label>{locale === "zh-CN" ? "翻译方式" : "Translation provider"}</label>
              <select value={provider} disabled={busy} onChange={(event) => {
                setProvider(event.target.value);
                setExternalConfirmed(false);
                setResult("");
              }}>
                {providers.map((entry) => (
                  <option key={entry.id} value={entry.id} disabled={!entry.available}>
                    {locale === "zh-CN" ? entry.name_zh : entry.name}{entry.available ? "" : locale === "zh-CN" ? "（不可用）" : " (unavailable)"}
                  </option>
                ))}
              </select>
            </div>
            <span className="chip">{source === "zh" ? "中文" : "English"} → {target === "en" ? "English" : "中文"}</span>
          </div>
          {selectedProvider?.external && (
            <div className="callout warn">
              {locale === "zh-CN"
                ? provider === "mymemory"
                  ? "隐私提示：文本会发送到 MyMemory 公共服务，适合公开或已脱敏内容；公共服务可能限流。"
                  : "隐私与费用取决于你配置的大模型提供方；请求会记录模型与 token 用量。"
                : provider === "mymemory"
                  ? "Privacy: text is sent to the public MyMemory service. Use public or de-identified text; rate limits apply."
                  : "Privacy and cost follow your configured LLM provider; model and token usage are audited."}
            </div>
          )}
          {provider === "mymemory" && (
            <label className="check-row">
              <input type="checkbox" checked={externalConfirmed} disabled={busy} onChange={(event) => setExternalConfirmed(event.target.checked)} />
              <span>{locale === "zh-CN" ? "我确认文本可以发送到 MyMemory 公共服务" : "I confirm this text may be sent to the public MyMemory service"}</span>
            </label>
          )}
          {busy && jobId && (
            <div className="translation-progress">
              <progress max={1} value={progress} />
              <span>{Math.round(progress * 100)}% · {progressMessage}</span>
            </div>
          )}
          <div className="field"><label>{locale === "zh-CN" ? "原文" : "Source text"}</label><textarea rows={selectionOnly ? 3 : 7} value={text} readOnly /></div>
          {result && <div className="field"><label>{locale === "zh-CN" ? "译文（请校对专业术语）" : "Translation (verify specialist terminology)"}</label><textarea rows={selectionOnly ? 3 : 7} value={result} onChange={(event) => setResult(event.target.value)} />{note && <span className="hint">{note}</span>}</div>}
        </div>
        <footer>
          <button className="btn" disabled={busy} onClick={onClose}>{locale === "zh-CN" ? "关闭" : "Close"}</button>
          <div className="grow" />
          {busy && jobId && <button className="btn danger" disabled={cancelling} onClick={async () => {
            setCancelling(true);
            try { await endpoints.system.cancelJob(jobId); }
            catch (error) { reportError(error, locale === "zh-CN" ? "取消翻译" : "cancelling translation"); setCancelling(false); }
          }}>{cancelling ? (locale === "zh-CN" ? "正在取消…" : "Cancelling…") : (locale === "zh-CN" ? "取消翻译" : "Cancel translation")}</button>}
          {result && <button className="btn" onClick={() => void copy()}>{locale === "zh-CN" ? "复制译文" : "Copy"}</button>}
          {!selectionOnly && result && <button className="btn primary" onClick={() => onApply(result)}>{locale === "zh-CN" ? "放入对照栏" : "Use as paired text"}</button>}
          {!busy && <button className="btn primary" disabled={!text.trim() || (provider === "mymemory" && !externalConfirmed)} onClick={() => void translate()}>{locale === "zh-CN" ? "开始翻译" : "Translate"}</button>}
        </footer>
      </div>
    </div>
  );
}

function SectionDialog({
  mode,
  projectId,
  section,
  onClose,
  onDone,
}: {
  mode: "add" | "edit";
  projectId: string;
  section: Section | null;
  onClose: () => void;
  onDone: (key: string) => Promise<void>;
}) {
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const dirty = useStore((s) => s.dirtySections);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    key: section?.key ?? "",
    title: section?.title ?? "",
    title_zh: section?.title_zh ?? "",
    target_words: section?.target_words ?? 0,
    target_words_zh: section?.target_words_zh ?? 0,
    guidance: section?.guidance ?? "",
  });

  async function save() {
    if (!form.title.trim()) {
      notify({ kind: "warning", message: locale === "zh-CN" ? "章节名称不能为空" : "Section title is required" });
      return;
    }
    setBusy(true);
    try {
      if (mode === "add") {
        const result = await endpoints.writing.createSection(projectId, form);
        await onDone(result.section.key);
      } else if (section) {
        await endpoints.writing.updateSection(projectId, section.key, {
          title: form.title,
          title_zh: form.title_zh,
          target_words: Math.max(0, form.target_words),
          target_words_zh: Math.max(0, form.target_words_zh),
          guidance: form.guidance,
        });
        await onDone(section.key);
      }
      notify({ kind: "success", message: locale === "zh-CN" ? "章节设置已保存" : "Section saved" });
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "保存章节" : "saving the section");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!section) return;
    const hasDirty = dirty[section.key] !== undefined;
    const question = locale === "zh-CN"
      ? `${hasDirty ? "该章节有未保存文本。" : ""}删除“${section.title}”？删除前会保留本地 Git/快照中的既有版本，但当前章节将从手稿中移除。`
      : `${hasDirty ? "This section has unsaved text. " : ""}Delete “${section.title}”? Existing Git/snapshot history remains, but the current section will be removed.`;
    if (!window.confirm(question)) return;
    setBusy(true);
    try {
      await endpoints.writing.deleteSection(projectId, section.key);
      await onDone("");
      notify({ kind: "success", message: locale === "zh-CN" ? "章节已删除" : "Section deleted" });
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "删除章节" : "deleting the section");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <header>
          <span>{mode === "add" ? (locale === "zh-CN" ? "新增章节" : "Add section") : (locale === "zh-CN" ? "章节设置" : "Section settings")}</span>
          <button className="btn icon sm" onClick={onClose} aria-label={locale === "zh-CN" ? "关闭章节设置" : "Close section settings"}>✕</button>
        </header>
        <div className="modal-body">
          {mode === "add" && (
            <div className="field">
              <label htmlFor="section-key-input">{locale === "zh-CN" ? "稳定键（可留空自动生成）" : "Stable key (optional)"}</label>
              <input id="section-key-input" value={form.key} onChange={(event) => setForm({ ...form, key: event.target.value })} placeholder="related-work" />
            </div>
          )}
          <div className="row">
            <div className="field grow"><label htmlFor="section-title-input">{locale === "zh-CN" ? "英文名称" : "English title"}</label><input id="section-title-input" autoFocus value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /></div>
            <div className="field grow"><label htmlFor="section-title-zh-input">{locale === "zh-CN" ? "中文名称" : "Chinese title"}</label><input id="section-title-zh-input" value={form.title_zh} onChange={(event) => setForm({ ...form, title_zh: event.target.value })} /></div>
          </div>
          <div className="row">
            <div className="field grow"><label htmlFor="section-primary-target-input">{locale === "zh-CN" ? "主语言目标字数" : "Primary target"}</label><input id="section-primary-target-input" type="number" min={0} value={form.target_words} onChange={(event) => setForm({ ...form, target_words: Number(event.target.value) || 0 })} /></div>
            <div className="field grow"><label htmlFor="section-paired-target-input">{locale === "zh-CN" ? "对照语言目标字数" : "Paired target"}</label><input id="section-paired-target-input" type="number" min={0} value={form.target_words_zh} onChange={(event) => setForm({ ...form, target_words_zh: Number(event.target.value) || 0 })} /></div>
          </div>
          <div className="field"><label htmlFor="section-guidance-input">{locale === "zh-CN" ? "写作要求" : "Writing brief"}</label><textarea id="section-guidance-input" rows={4} value={form.guidance} onChange={(event) => setForm({ ...form, guidance: event.target.value })} /></div>
        </div>
        <footer>
          {mode === "edit" && <button className="btn danger" disabled={busy} onClick={() => void remove()}>{locale === "zh-CN" ? "删除章节" : "Delete section"}</button>}
          <div className="grow" />
          <button className="btn" disabled={busy} onClick={onClose}>{locale === "zh-CN" ? "取消" : "Cancel"}</button>
          <button className="btn primary" disabled={busy} onClick={() => void save()}>{busy ? (locale === "zh-CN" ? "保存中…" : "Saving…") : (locale === "zh-CN" ? "保存" : "Save")}</button>
        </footer>
      </div>
    </div>
  );
}

function ManuscriptSyncBanner({
  projectId,
  revision,
}: {
  projectId: string;
  revision: string;
}) {
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const reloadDocument = useStore((s) => s.reloadDocument);
  const [status, setStatus] = useState<ManuscriptSyncStatus | null>(null);
  const [busy, setBusy] = useState<"database" | "files" | "merge" | "">("");

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      try {
        const next = await endpoints.writing.syncStatus(projectId);
        if (!cancelled) setStatus(next);
      } catch {
        // Saving reports actionable API errors. A background status probe
        // should not create a repeated toast every five seconds.
      }
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [projectId, revision]);

  if (
    !status ||
    (status.state === "in_sync" && !status.baseline_error) ||
    (status.state === "untracked_equal" && !status.baseline_error)
  ) {
    return null;
  }

  const stateText =
    locale === "zh-CN"
      ? {
          database_changed: "数据库手稿已修改，磁盘文件仍是旧版本",
          disk_changed: "检测到 PaperCreator 之外的文件修改",
          diverged: "数据库与磁盘文件都已修改",
          database_only: "数据库有手稿，但尚未建立磁盘同步基线",
          disk_only: "磁盘有手稿，但数据库尚未建立同步基线",
          untracked_divergence: "旧项目的数据库与文件不同，无法自动判断哪一侧正确",
          untracked_equal: "同步基线需要重新建立",
          in_sync: "同步状态文件需要修复",
        }[status.state]
      : {
          database_changed: "The database manuscript changed; disk files are older",
          disk_changed: "Files were changed outside PaperCreator",
          diverged: "Both the database and manuscript files changed",
          database_only: "The database has a manuscript but no disk baseline",
          disk_only: "Disk has a manuscript but no database baseline",
          untracked_divergence: "Legacy DB and files differ; neither side can be guessed",
          untracked_equal: "The sync baseline needs to be rebuilt",
          in_sync: "The sync state file needs repair",
        }[status.state];

  async function resolve(preference: "database" | "files") {
    const useDatabase = preference === "database";
    const warning = useDatabase
      ? locale === "zh-CN"
        ? "将以数据库内容覆盖手稿文件。如文件包含未确认修改，会先备份到项目 .papercreator/conflicts/。继续？"
        : "Database text will replace manuscript files. If files contain unacknowledged changes, they are backed up under the project .papercreator/conflicts first. Continue?"
      : locale === "zh-CN"
        ? "将以磁盘文件覆盖数据库章节，并放弃编辑器中尚未保存的文字。数据库一定先创建快照；如有未同步 DB 修改还会保留冲突副本。继续？"
        : "Disk files will replace database sections and discard unsaved editor text. A DB snapshot is always created first; conflicting DB changes also receive a recovery copy. Continue?";
    if (!window.confirm(warning)) return;
    setBusy(preference);
    try {
      const result = useDatabase
        ? await endpoints.writing.flush(projectId, true)
        : await endpoints.writing.reindex(projectId, true);
      if (!useDatabase) {
        useStore.setState({ dirtySections: {} });
      }
      await reloadDocument();
      if (useDatabase) {
        // A failed section save updates the DB before its protective disk flush
        // returns 409. Once the user explicitly chooses the DB, that exact text
        // is fully persisted and must no longer look unsaved. Preserve any newer
        // typing by clearing only dirty values equal to the reloaded DB section.
        const current = useStore.getState();
        const persisted = new Map(
          (current.document?.sections ?? []).map((section) => [section.key, section.content]),
        );
        const dirtySections = Object.fromEntries(
          Object.entries(current.dirtySections).filter(
            ([key, value]) => persisted.get(key) !== value,
          ),
        );
        useStore.setState({ dirtySections });
      }
      setStatus(result.sync as ManuscriptSyncStatus);
      const backup = result.safety_backup as { path?: string } | null;
      const snapshot = result.safety_snapshot as { id?: string } | null;
      notify({
        kind: "success",
        message:
          locale === "zh-CN"
            ? useDatabase
              ? "已以数据库为准完成同步"
              : "已以磁盘文件为准完成同步"
            : useDatabase
              ? "Synchronized from the database"
              : "Synchronized from disk files",
        detail:
          backup?.path ||
          (snapshot?.id
            ? (locale === "zh-CN" ? "安全快照：" : "Safety snapshot: ") + snapshot.id
            : undefined),
      });
    } catch (error) {
      reportError(
        error,
        locale === "zh-CN" ? "解决手稿冲突" : "resolving manuscript conflict",
      );
    } finally {
      setBusy("");
    }
  }

  async function mergeDisjoint() {
    if (!status?.can_auto_merge) return;
    const databaseKeys = status.section_changes.database.join(", ");
    const diskKeys = status.section_changes.disk.join(", ");
    const warning = locale === "zh-CN"
      ? `确认合并不同章节的修改？\n\n数据库修改：${databaseKeys}\n磁盘修改：${diskKeys}\n\n合并前会创建数据库快照，并同时备份数据库与磁盘镜像。`
      : `Merge changes made to different sections?\n\nDatabase changes: ${databaseKeys}\nDisk changes: ${diskKeys}\n\nA database snapshot and backups of both mirrors are created first.`;
    if (!window.confirm(warning)) return;
    setBusy("merge");
    try {
      const result = await endpoints.writing.mergeDisjoint(
        projectId,
        status.merge_preview_token,
      );
      await reloadDocument();
      const current = useStore.getState();
      const persisted = new Map(
        (current.document?.sections ?? []).map((section) => [section.key, section.content]),
      );
      const dirtySections = Object.fromEntries(
        Object.entries(current.dirtySections).filter(
          ([key, value]) => persisted.get(key) !== value,
        ),
      );
      useStore.setState({ dirtySections });
      setStatus(result.sync);
      notify({
        kind: "success",
        message: locale === "zh-CN"
          ? "不同章节的数据库与磁盘修改已安全合并"
          : "Disjoint database and disk section changes were merged safely",
        detail: locale === "zh-CN"
          ? `数据库：${result.merged_from_database.join(", ")}；磁盘：${result.merged_from_disk.join(", ")}`
          : `Database: ${result.merged_from_database.join(", ")}; disk: ${result.merged_from_disk.join(", ")}`,
      });
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "合并手稿章节修改" : "merging manuscript section changes");
      try {
        setStatus(await endpoints.writing.syncStatus(projectId));
      } catch {
        // The main error already contains recovery guidance.
      }
    } finally {
      setBusy("");
    }
  }

  return (
    <div
      className="guidance"
      style={{
        margin: "8px 12px",
        borderColor: "var(--error)",
        background: "color-mix(in srgb, var(--error) 8%, var(--bg-panel))",
      }}
    >
      <div className="row wrap">
        <strong>⚠ {locale === "zh-CN" ? "手稿同步冲突" : "Manuscript sync conflict"}</strong>
        <span className="chip err">{status.state}</span>
        <span className="grow">{stateText}</span>
        <button
          className="btn sm"
          disabled={Boolean(busy)}
          onClick={() => void window.papercreator?.shell.openPath(status.path)}
        >
          {locale === "zh-CN" ? "查看文件" : "Open files"}
        </button>
        {status.can_auto_merge && (
          <button
            className="btn sm primary"
            disabled={Boolean(busy)}
            onClick={() => void mergeDisjoint()}
          >
            {busy === "merge"
              ? (locale === "zh-CN" ? "正在合并…" : "Merging…")
              : (locale === "zh-CN" ? "合并不同章节" : "Merge disjoint sections")}
          </button>
        )}
        <button
          className="btn sm"
          disabled={Boolean(busy)}
          onClick={() => void resolve("files")}
        >
          {busy === "files"
            ? locale === "zh-CN" ? "正在导入…" : "Importing…"
            : locale === "zh-CN" ? "以文件为准" : "Use files"}
        </button>
        <button
          className="btn sm primary"
          disabled={Boolean(busy)}
          onClick={() => void resolve("database")}
        >
          {busy === "database"
            ? locale === "zh-CN" ? "正在写入…" : "Writing…"
            : locale === "zh-CN" ? "以数据库为准" : "Use database"}
        </button>
      </div>
      <div className="dim" style={{ marginTop: 6 }}>
        {locale === "zh-CN"
          ? "数据库章节：" + status.database.sections +
            "；磁盘文件：" + status.disk.files_count +
            "。PaperCreator 不会在未确认时覆盖变化的一侧。"
          : "DB sections: " + status.database.sections +
            "; disk files: " + status.disk.files_count +
            ". PaperCreator will not overwrite a changed side without confirmation."}
        {status.baseline_error ? " · " + status.baseline_error : ""}
      </div>
      {(status.section_changes.database.length > 0 || status.section_changes.disk.length > 0) && (
        <div className="dim" style={{ marginTop: 4 }}>
          {locale === "zh-CN"
            ? `数据库修改章节：${status.section_changes.database.join(", ") || "无"}；磁盘修改章节：${status.section_changes.disk.join(", ") || "无"}`
            : `DB-changed sections: ${status.section_changes.database.join(", ") || "none"}; disk-changed sections: ${status.section_changes.disk.join(", ") || "none"}`}
          {status.section_changes.conflicts.length > 0
            ? (locale === "zh-CN"
              ? `；重叠冲突：${status.section_changes.conflicts.join(", ")}`
              : `; overlapping conflicts: ${status.section_changes.conflicts.join(", ")}`)
            : ""}
        </div>
      )}
    </div>
  );
}

function BilingualBar() {
  const project = useStore((s) => s.project);
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const [status, setStatus] = useState<Record<string, any> | null>(null);
  const [bulkOpen, setBulkOpen] = useState(false);

  useEffect(() => {
    if (!project?.bilingual) return;
    void endpoints.writing
      .bilingual(project.id)
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [project?.id, project?.bilingual]);

  if (!project?.bilingual || !status) return null;
  const summary = status.summary as Record<string, number>;

  return (
    <div className="row" style={{ padding: "5px 12px", borderTop: "1px solid var(--border-soft)", flex: "none" }}>
      <span className="dim">{locale === "zh-CN" ? "中英对照：" : "Bilingual:"}</span>
      <span className="chip ok">
        {summary.aligned} {locale === "zh-CN" ? "已对齐" : "aligned"}
      </span>
      {summary.untranslated > 0 && (
        <span className="chip warn">
          {summary.untranslated} {locale === "zh-CN" ? "未翻译" : "untranslated"}
        </span>
      )}
      {summary.drifted > 0 && (
        <span
          className="chip err"
          title={
            locale === "zh-CN"
              ? "两个版本长度比例异常，可能其中一侧被单独修改过"
              : "The two versions' length ratio is off — one side was probably edited alone"
          }
        >
          {summary.drifted} {locale === "zh-CN" ? "可能偏移" : "drifted"}
        </span>
      )}
      <div className="grow" />
      <button
        className="btn sm"
        onClick={() => setBulkOpen(true)}
      >
        {locale === "zh-CN" ? "翻译全部" : "Translate all"}
      </button>
      <button
        className="btn sm"
        title={
          locale === "zh-CN"
            ? "把主语言与对照语言整体互换"
            : "Swap the primary and paired language throughout"
        }
        onClick={async () => {
          if (
            !window.confirm(
              locale === "zh-CN"
                ? "确认互换所有章节的主语言与对照语言吗？"
                : "Swap the primary and paired language for every section?",
            )
          ) return;
          const result = await endpoints.writing.swapLanguages(project.id);
          notify({
            kind: "success",
            message:
              locale === "zh-CN"
                ? `主语言已切换为 ${result.primary_language}`
                : `Primary language is now ${result.primary_language}`,
            detail:
              locale === "zh-CN"
                ? `已互换 ${result.sections_swapped} 个章节`
                : `${result.sections_swapped} sections swapped`,
          });
          void useStore.getState().reloadDocument();
        }}
      >
        {locale === "zh-CN" ? "互换语言" : "Swap languages"}
      </button>
      {bulkOpen && <BulkTranslationDialog onClose={() => setBulkOpen(false)} />}
    </div>
  );
}

function BulkTranslationDialog({ onClose }: { onClose: () => void }) {
  const project = useStore((s) => s.project);
  const document = useStore((s) => s.document);
  const locale = useStore((s) => s.locale);
  const hasLlm = useStore((s) => s.health?.llm.has_any ?? false);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const reloadDocument = useStore((s) => s.reloadDocument);
  const saveAllSections = useStore((s) => s.saveAllSections);
  const [provider, setProvider] = useState<"mymemory" | "llm">(hasLlm ? "llm" : "mymemory");
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [externalConfirmed, setExternalConfirmed] = useState(false);
  const [applyConfirmed, setApplyConfirmed] = useState(false);
  const [jobId, setJobId] = useState("");
  const [progress, setProgress] = useState(0);
  const [progressMessage, setProgressMessage] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [preview, setPreview] = useState<TranslationJobResult | null>(null);
  const previewJobId = useRef("");
  const candidates = (document?.sections ?? []).filter(
    (section) => section.content.trim() && (overwrite || !section.content_zh.trim()),
  );

  useEffect(() => {
    if (!project?.id) return;
    let active = true;
    void endpoints.system.jobs({ project_id: project.id, status: "done", limit: 30 })
      .then(({ items }) => {
        if (!active) return;
        const resumable = items.find((job) => {
          const result = job.result as Record<string, unknown>;
          return job.kind === "translation" && result?.mode === "project" &&
            result?.project_id === project.id && !result?.applied_at;
        });
        if (resumable) {
          previewJobId.current = resumable.id;
          setPreview(resumable.result as unknown as TranslationJobResult);
        }
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [project?.id]);

  async function run() {
    if (!project || !candidates.length) return;
    setBusy(true);
    setPreview(null);
    setApplyConfirmed(false);
    setProgress(0);
    try {
      await saveAllSections();
      const accepted = await endpoints.writing.startTranslationJob({
        project_id: project.id,
        section_keys: candidates.map((section) => section.key),
        source: project.language === "zh" ? "zh" : "en",
        target: project.language === "zh" ? "en" : "zh-CN",
        provider,
        overwrite,
        confirm_external: provider === "mymemory" ? externalConfirmed : false,
      });
      setJobId(accepted.job_id);
      previewJobId.current = accepted.job_id;
      setProgressMessage(locale === "zh-CN" ? "翻译任务已进入队列" : "Translation job queued");
      const result = (await waitForJob(accepted.job_id, (fraction, message) => {
        setProgress(fraction);
        setProgressMessage(message);
      })) as unknown as TranslationJobResult;
      setPreview(result);
      notify({
        kind: "success",
        message:
          locale === "zh-CN"
            ? `已生成 ${result.section_count ?? 0} 个章节的译文预览，手稿尚未改动`
            : `Generated a preview for ${result.section_count ?? 0} section(s); manuscript unchanged`,
      });
    } catch (error) {
      if (error instanceof JobFailureError && error.failure.cancelled) {
        notify({ kind: "warning", message: locale === "zh-CN" ? "翻译已取消，手稿未改动" : "Translation cancelled; manuscript unchanged" });
      } else {
        reportError(error, locale === "zh-CN" ? "生成批量翻译预览" : "generating bulk translation preview");
      }
    } finally {
      setBusy(false);
      setJobId("");
      setCancelling(false);
    }
  }

  async function apply() {
    if (!jobId && !preview) return;
    const completedJobId = jobId || "";
    // waitForJob clears the active UI id only in finally, so retain the durable
    // id on the preview itself through a separate ref.
    const targetJobId = previewJobId.current || completedJobId;
    if (!targetJobId || !applyConfirmed) return;
    setBusy(true);
    try {
      const result = await endpoints.writing.applyTranslationJob(targetJobId);
      await reloadDocument();
      notify({
        kind: "success",
        message: locale === "zh-CN" ? `已一次性写入 ${result.sections_applied} 个章节` : `Applied ${result.sections_applied} sections in one write`,
        detail: locale === "zh-CN" ? `恢复快照 ${result.snapshot_id}` : `Recovery snapshot ${result.snapshot_id}`,
      });
      onClose();
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "应用翻译预览" : "applying translation preview");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={() => !busy && onClose()}>
      <div className="modal wide" onClick={(event) => event.stopPropagation()}>
        <header><span>{locale === "zh-CN" ? "批量翻译手稿" : "Translate the manuscript"}</span><button className="btn icon sm" disabled={busy} onClick={onClose} aria-label={locale === "zh-CN" ? "关闭批量翻译" : "Close bulk translation"}>×</button></header>
        <div className="modal-body">
          <div className="field"><label>{locale === "zh-CN" ? "翻译服务" : "Translation provider"}</label><select value={provider} disabled={busy || Boolean(preview)} onChange={(event) => { setProvider(event.target.value as "mymemory" | "llm"); setExternalConfirmed(false); }}><option value="mymemory">MyMemory · {locale === "zh-CN" ? "公共免费服务" : "free public service"}</option><option value="llm" disabled={!hasLlm}>{locale === "zh-CN" ? "已配置 LLM（专业上下文）" : "Configured LLM (professional context)"}</option></select></div>
          <p className={provider === "mymemory" ? "warn-text" : "dim"}>{provider === "mymemory" ? (locale === "zh-CN" ? "章节正文会发送到 api.mymemory.translated.net；公共服务有额度限制，不适合敏感或未公开内容。" : "Section text is sent to api.mymemory.translated.net. Public limits apply; do not use for sensitive or unpublished text.") : (locale === "zh-CN" ? "正文会发送到当前配置的 LLM 提供方并产生相应费用。" : "Text is sent to the configured LLM provider and may incur its normal cost.")}</p>
          {provider === "mymemory" && <label className="check-row"><input type="checkbox" checked={externalConfirmed} disabled={busy || Boolean(preview)} onChange={(event) => setExternalConfirmed(event.target.checked)} /><span>{locale === "zh-CN" ? "我确认这些章节可以发送到 MyMemory 公共服务" : "I confirm these sections may be sent to the public MyMemory service"}</span></label>}
          <label className="check-row"><input type="checkbox" checked={overwrite} disabled={busy || Boolean(preview)} onChange={(event) => setOverwrite(event.target.checked)} /><span>{locale === "zh-CN" ? "重新翻译并覆盖已有对照文本" : "Retranslate and overwrite existing paired text"}</span></label>
          <div className="bulk-translation-summary"><strong>{locale === "zh-CN" ? `${candidates.length} 个章节将被翻译` : `${candidates.length} section(s) will be translated`}</strong><div className="dim">{candidates.map((section) => section.title_zh || section.title).join(" · ") || (locale === "zh-CN" ? "没有需要翻译的章节" : "No sections need translation")}</div></div>
          {busy && jobId && <div className="translation-progress"><progress max={1} value={progress} /><span>{Math.round(progress * 100)}% · {progressMessage}</span></div>}
          {preview && <div className="translation-preview">
            <strong>{locale === "zh-CN" ? "完整译文预览（尚未写入）" : "Complete translation preview (not applied)"}</strong>
            <div className="dim">{locale === "zh-CN" ? `${preview.section_count} 个章节 · ${preview.translated_characters?.toLocaleString() ?? 0} 字符` : `${preview.section_count} sections · ${preview.translated_characters?.toLocaleString() ?? 0} characters`}</div>
            {(preview.sections ?? []).map((section) => <details key={section.key}><summary>{section.title_zh || section.title} · {section.translated_characters.toLocaleString()}</summary><textarea rows={8} value={section.text} readOnly /></details>)}
            <label className="check-row"><input type="checkbox" checked={applyConfirmed} disabled={busy} onChange={(event) => setApplyConfirmed(event.target.checked)} /><span>{locale === "zh-CN" ? "我已检查译文，确认一次性写入上述章节" : "I reviewed the translations and confirm applying all sections at once"}</span></label>
          </div>}
        </div>
        <footer>
          <button className="btn" disabled={busy} onClick={onClose}>{locale === "zh-CN" ? "关闭" : "Close"}</button>
          <div className="grow" />
          {busy && jobId && <button className="btn danger" disabled={cancelling} onClick={async () => { setCancelling(true); try { await endpoints.system.cancelJob(jobId); } catch (error) { reportError(error, locale === "zh-CN" ? "取消翻译" : "cancelling translation"); setCancelling(false); } }}>{cancelling ? (locale === "zh-CN" ? "正在取消…" : "Cancelling…") : (locale === "zh-CN" ? "取消翻译" : "Cancel translation")}</button>}
          {!busy && !preview && <button className="btn primary" disabled={candidates.length === 0 || (provider === "mymemory" && !externalConfirmed)} onClick={async () => { await run(); }}>{locale === "zh-CN" ? "生成完整预览" : "Generate complete preview"}</button>}
          {!busy && preview && <button className="btn primary" disabled={!applyConfirmed} onClick={() => void apply()}>{locale === "zh-CN" ? "确认并一次性写入" : "Confirm and apply once"}</button>}
          {!busy && preview && <button className="btn" onClick={() => { setPreview(null); setApplyConfirmed(false); }}>{locale === "zh-CN" ? "重新生成预览" : "Generate another preview"}</button>}
        </footer>
      </div>
    </div>
  );
}

function TemplatePicker({ onDone }: { onDone: () => void }) {
  const project = useStore((s) => s.project);
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const [templates, setTemplates] = useState<PaperTemplate[]>([]);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");

  useEffect(() => {
    void endpoints.writing.templates().then((result) => setTemplates(result.items));
  }, []);

  async function apply(templateId: string, replace: boolean) {
    if (!project) return;
    setBusy(true);
    try {
      const result = await endpoints.writing.applyTemplate(project.id, {
        template_id: templateId,
        replace,
      });
      notify({
        kind: "success",
        message:
          locale === "zh-CN"
            ? `已应用结构：${result.document.sections.length} 个章节`
            : `Structure applied: ${result.document.sections.length} sections`,
      });
      await useStore.getState().reloadDocument();
      onDone();
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "应用结构模板" : "applying the template");
    } finally {
      setBusy(false);
    }
  }

  const categories = Array.from(new Set(templates.map((item) => item.category || "general")));
  const visible = templates.filter((template) => {
    if (category && (template.category || "general") !== category) return false;
    const haystack = `${template.name} ${template.name_zh} ${template.description} ${template.description_zh || ""}`.toLowerCase();
    return haystack.includes(query.trim().toLowerCase());
  });

  return (
    <>
      <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
        {locale === "zh-CN"
          ? "模板只添加缺失的章节，不会覆盖已有内容。"
          : "Applying a template adds missing sections; existing content is never overwritten."}
      </p>
      <div className="row wrap" style={{ marginBottom: 10 }}>
        <input
          className="grow"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={locale === "zh-CN" ? "搜索 SCI、SSCI、会议、综述、海报等结构" : "Search SCI, SSCI, conference, review, poster…"}
        />
        <select value={category} onChange={(event) => setCategory(event.target.value)}>
          <option value="">{locale === "zh-CN" ? "全部类别" : "All categories"}</option>
          {categories.map((item) => (
            <option key={item} value={item}>{templateCategoryLabel(item, locale)}</option>
          ))}
        </select>
      </div>
      <div className="cards">
        {visible.map((template) => (
          <div key={template.id} className="card">
            <h3>{locale === "zh-CN" ? template.name_zh : template.name}</h3>
            <span className="chip">{templateCategoryLabel(template.category || "general", locale)}</span>
            <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
              {locale === "zh-CN" ? template.description_zh || template.description : template.description}
            </p>
            <div className="row wrap" style={{ gap: 4, marginBottom: 10 }}>
              {template.sections.map((section) => (
                <span
                  key={section.key}
                  className="chip"
                  title={`${section.target_words} ${locale === "zh-CN" ? "词" : "words"}`}
                >
                  {locale === "zh-CN" ? section.title_zh : section.title}
                </span>
              ))}
            </div>
            <div className="row">
              <button
                className="btn sm primary"
                disabled={busy}
                onClick={() => void apply(template.id, false)}
              >
                {locale === "zh-CN" ? "应用" : "Apply"}
              </button>
              <span className="dim">
                {template.section_count} {locale === "zh-CN" ? "章节" : "sections"} · ~
                {template.total_words} {locale === "zh-CN" ? "词" : "words"}
              </span>
            </div>
          </div>
        ))}
      </div>
      {visible.length === 0 && <div className="empty">{locale === "zh-CN" ? "没有匹配的结构模板。" : "No matching structure templates."}</div>}
    </>
  );
}

function templateCategoryLabel(category: string, locale: "zh-CN" | "en-US") {
  if (locale !== "zh-CN") return category.replace(/-/g, " ");
  return ({
    general: "通用",
    "academic-journal": "学术期刊",
    conference: "会议",
    "poster-presentation": "海报 / 展示",
    book: "书籍",
    thesis: "学位论文",
  } as Record<string, string>)[category] || category;
}

function sectionStatusLabel(status: string, locale: "zh-CN" | "en-US") {
  if (locale !== "zh-CN") return status;
  return ({
    empty: "空白",
    drafting: "撰写中",
    drafted: "已起草",
    reviewed: "已审阅",
    final: "定稿",
  } as Record<string, string>)[status] || status;
}

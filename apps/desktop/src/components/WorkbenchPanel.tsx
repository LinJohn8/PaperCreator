/** Workbench identity, storage contract and classified import hub. */

import { useEffect, useMemo, useRef, useState } from "react";

import * as endpoints from "../api/endpoints";
import { JobFailureError, waitForJob } from "../api/events";
import type {
  WorkbenchCategory,
  WorkbenchImportResult,
  WorkbenchResourceKind,
} from "../api/types";
import { useStore } from "../state/store";

const GLYPHS: Record<WorkbenchResourceKind, string> = {
  idea: "◇",
  reference_paper: "▤",
  own_paper: "▧",
  code_project: "</>",
  dataset: "▦",
  supplementary: "◫",
  inbox: "↓",
};

export function WorkbenchPanel({ onNewProject }: { onNewProject: () => void }) {
  const info = useStore((state) => state.workbench);
  const resources = useStore((state) => state.workbenchResources);
  const locale = useStore((state) => state.locale);
  const loadWorkbench = useStore((state) => state.loadWorkbench);
  const loadResources = useStore((state) => state.loadWorkbenchResources);
  const [importing, setImporting] = useState<WorkbenchCategory | null>(null);

  useEffect(() => {
    void Promise.all([loadWorkbench(), loadResources()]);
  }, [loadResources, loadWorkbench]);

  const recentByKind = useMemo(() => {
    const result = new Map<string, typeof resources>();
    for (const resource of resources) {
      const items = result.get(resource.kind) ?? [];
      if (items.length < 3) items.push(resource);
      result.set(resource.kind, items);
    }
    return result;
  }, [resources]);

  if (!info) return null;

  return (
    <>
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="row">
          <div className="grow">
            <h3>{locale === "zh-CN" ? "当前工作台" : "Current workbench"}</h3>
            <div className="mono" style={{ userSelect: "text" }}>
              {info.workbench}
            </div>
            <p className="muted" style={{ marginBottom: 0 }}>
              {locale === "zh-CN"
                ? `PaperCreator 管理的全部项目数据位于 ${info.managed_directory_name}/；复制这个目录即可备份完整工作台。`
                : `All PaperCreator-managed project data lives in ${info.managed_directory_name}/; copy that directory to back up the complete workbench.`}
            </p>
          </div>
          <button
            className="btn"
            onClick={() => void window.papercreator?.shell.openPath(info.managed_directory)}
          >
            {locale === "zh-CN" ? "打开系统目录" : "Open managed folder"}
          </button>
          <button
            className="btn"
            onClick={() => void window.papercreator?.workbench.choose()}
          >
            {locale === "zh-CN" ? "切换工作台" : "Switch workbench"}
          </button>
        </div>
      </div>

      <div className="row" style={{ alignItems: "flex-end", marginBottom: 8 }}>
        <div className="grow">
          <h2 style={{ marginBottom: 3 }}>
            {locale === "zh-CN" ? "新建与导入" : "Create and import"}
          </h2>
          <p className="sub" style={{ margin: 0 }}>
            {locale === "zh-CN"
              ? "“新论文”是可写作项目；Idea、参考论文、自己的论文和代码是项目可复用的输入资料。它们不会混在一起。"
              : "A new paper is a writing project. Ideas, references, your prior papers and code are reusable inputs, kept in separate categories."}
          </p>
        </div>
      </div>

      <div className="cards" style={{ marginBottom: 20 }}>
        <div className="card" style={{ borderColor: "var(--accent)" }}>
          <div className="row">
            <span className="big" aria-hidden="true">＋</span>
            <div className="grow">
              <h3>{locale === "zh-CN" ? "新论文" : "New paper"}</h3>
              <p className="muted">
                {locale === "zh-CN"
                  ? "创建论文手稿、章节结构、文献集合、AI 写作流程和独立 Git 版本库。"
                  : "Create a manuscript, section plan, paper collection, AI workflow and independent Git history."}
              </p>
              <span className="dim mono">{info.projects_directory}</span>
            </div>
          </div>
          <button className="btn primary" onClick={onNewProject}>
            {locale === "zh-CN" ? "新建论文项目" : "Create paper project"}
          </button>
        </div>

        {info.categories.map((category) => (
          <div className="card" key={category.kind}>
            <div className="row" style={{ alignItems: "flex-start" }}>
              <span className="big mono" aria-hidden="true">
                {GLYPHS[category.kind]}
              </span>
              <div className="grow">
                <h3>
                  {locale === "zh-CN" ? category.label_zh : category.label}{" "}
                  <span className="chip">{category.count}</span>
                </h3>
                <p className="muted" style={{ minHeight: 38 }}>
                  {locale === "zh-CN"
                    ? category.description_zh
                    : category.description}
                </p>
              </div>
            </div>
            {(recentByKind.get(category.kind) ?? []).map((resource) => {
              const extraction = resource.metadata.extraction as Record<string, any> | undefined;
              return (
                <div key={resource.id} className="workbench-recent-resource">
                  <button
                    className="row"
                    title={resource.path}
                    onClick={() => void window.papercreator?.shell.openPath(resource.path)}
                    style={{ width: "100%", padding: "2px 0", textAlign: "left" }}
                  >
                    <span className="grow truncate">{resource.title}</span>
                    <span className="dim">{formatBytes(resource.size_bytes)}</span>
                  </button>
                  {category.kind === "own_paper" && extraction && (
                    <div className="row wrap" style={{ gap: 4 }}>
                      <span className={`chip ${Number(extraction.characters) > 0 ? "ok" : "warn"}`}>
                        {Number(extraction.characters || 0).toLocaleString()} {locale === "zh-CN" ? "字符" : "chars"}
                      </span>
                      <span className="chip">{String(extraction.method || "unknown")}</span>
                      {Boolean(extraction.truncated) && <span className="chip warn">{locale === "zh-CN" ? "已截断" : "truncated"}</span>}
                      {((extraction.warnings as string[]) ?? []).length > 0 && (
                        <span className="chip warn" title={(extraction.warnings as string[]).join(" · ")}>
                          {locale === "zh-CN" ? "提取告警" : "extraction warning"}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn sm" onClick={() => setImporting(category)}>
                {category.kind === "idea"
                  ? locale === "zh-CN" ? "记录 Idea" : "Add idea"
                  : locale === "zh-CN" ? "导入" : "Import"}
              </button>
              <button
                className="btn sm"
                onClick={() => void window.papercreator?.shell.openPath(category.path)}
              >
                {locale === "zh-CN" ? "打开目录" : "Open folder"}
              </button>
            </div>
          </div>
        ))}
      </div>

      {importing && (
        <ImportResourceModal category={importing} onClose={() => setImporting(null)} />
      )}
    </>
  );
}

function ImportResourceModal({
  category,
  onClose,
}: {
  category: WorkbenchCategory;
  onClose: () => void;
}) {
  const locale = useStore((state) => state.locale);
  const project = useStore((state) => state.project);
  const loadWorkbench = useStore((state) => state.loadWorkbench);
  const loadResources = useStore((state) => state.loadWorkbenchResources);
  const loadLibrary = useStore((state) => state.loadLibrary);
  const reloadDocument = useStore((state) => state.reloadDocument);
  const notify = useStore((state) => state.notify);
  const reportError = useStore((state) => state.reportError);
  const [busy, setBusy] = useState(false);
  const [sourcePath, setSourcePath] = useState("");
  const [sourceIsDirectory, setSourceIsDirectory] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [activeJobId, setActiveJobId] = useState("");
  const [progress, setProgress] = useState(0);
  const [progressMessage, setProgressMessage] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const detached = useRef(false);

  const supportsFolder = ["code_project", "dataset", "supplementary", "inbox"].includes(
    category.kind,
  );

  async function chooseFile() {
    const filters = fileFilters(category.kind);
    const selected = await window.papercreator?.dialog.openFile(filters);
    if (selected) {
      setSourcePath(selected);
      setSourceIsDirectory(false);
    }
  }

  async function chooseFolder() {
    const selected = await window.papercreator?.dialog.openDirectory({
      title: locale === "zh-CN" ? `选择${category.label_zh}目录` : `Choose ${category.label}`,
    });
    if (selected) {
      setSourcePath(selected);
      setSourceIsDirectory(true);
    }
  }

  async function submit() {
    if (category.kind === "idea" && !title.trim() && !description.trim()) {
      notify({ kind: "warning", message: locale === "zh-CN" ? "请填写 Idea" : "Describe the idea" });
      return;
    }
    if (category.kind !== "idea" && !sourcePath) {
      notify({ kind: "warning", message: locale === "zh-CN" ? "请先选择文件或目录" : "Choose a file or folder" });
      return;
    }
    setBusy(true);
    try {
      const request = {
        kind: category.kind,
        source_path: sourcePath,
        title,
        content: category.kind === "idea" ? description : "",
        description,
        project_id: project?.id ?? "",
      };
      let result: WorkbenchImportResult;
      if (sourceIsDirectory) {
        const accepted = await endpoints.workbench.startDirectoryImport(request);
        setActiveJobId(accepted.job_id);
        setProgress(0);
        setProgressMessage(
          locale === "zh-CN" ? "正在排队扫描目录…" : "Queued for directory scan…",
        );
        result = (await waitForJob(accepted.job_id, (fraction, message) => {
          setProgress(fraction);
          setProgressMessage(message);
        })) as unknown as WorkbenchImportResult;
      } else {
        result = await endpoints.workbench.importResource(request);
      }
      await Promise.all([
        loadWorkbench(),
        loadResources(),
        loadLibrary(),
        project ? reloadDocument() : Promise.resolve(),
      ]);
      notify({
        kind: result.warnings?.length ? "warning" : "success",
        message: locale === "zh-CN" ? "已导入工作台" : "Imported into the workbench",
        detail:
          result.warnings?.join("; ") ||
          (locale === "zh-CN"
            ? `已复制到 ${result.resource.managed_path}`
            : `Managed copy: ${result.resource.managed_path}`),
      });
      if (!detached.current) onClose();
    } catch (error) {
      if (error instanceof JobFailureError && error.failure.cancelled) {
        notify({
          kind: "info",
          message: locale === "zh-CN" ? "目录导入已取消" : "Directory import cancelled",
          detail:
            locale === "zh-CN"
              ? "已停止复制并清理未完成的 .partial 托管副本。"
              : "Copying stopped and the unfinished .partial managed copy was cleaned.",
        });
      } else if (error instanceof JobFailureError) {
        const errorCode = String(error.failure.error_code ?? "resource_import_failed");
        const hint = String(error.failure.hint ?? "");
        notify({
          kind: "error",
          message:
            locale === "zh-CN"
              ? `目录导入失败：${error.message}`
              : `Directory import failed: ${error.message}`,
          detail: [errorCode, hint].filter(Boolean).join(" · "),
        });
      } else {
        reportError(error, locale === "zh-CN" ? "导入资源" : "importing the resource");
      }
    } finally {
      setBusy(false);
      setActiveJobId("");
      setCancelling(false);
    }
  }

  async function cancelImport() {
    if (!activeJobId || cancelling) return;
    setCancelling(true);
    setProgressMessage(
      locale === "zh-CN"
        ? "已请求取消；正在完成当前分块并清理临时副本…"
        : "Cancellation requested; finishing the current chunk and cleaning staging…",
    );
    try {
      await endpoints.system.cancelJob(activeJobId);
    } catch (error) {
      setCancelling(false);
      reportError(error, locale === "zh-CN" ? "取消目录导入" : "cancelling the import");
    }
  }

  function closeOrDetach() {
    if (busy) detached.current = true;
    onClose();
  }

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <header>
          <span>
            {category.kind === "idea"
              ? locale === "zh-CN" ? "记录研究 Idea" : "Add a research idea"
              : `${locale === "zh-CN" ? "导入" : "Import"} ${
                  locale === "zh-CN" ? category.label_zh : category.label
                }`}
          </span>
          <button className="btn icon sm" onClick={closeOrDetach} title={
            busy
              ? locale === "zh-CN" ? "任务会在后台继续" : "The job will continue in the background"
              : undefined
          } aria-label={busy
            ? locale === "zh-CN" ? "关闭并转入后台" : "Close and continue in background"
            : locale === "zh-CN" ? "关闭导入" : "Close import"
          }>✕</button>
        </header>
        <div className="modal-body">
          <div className="card">
            <strong>{locale === "zh-CN" ? "存储规则" : "Storage rule"}</strong>
            <p className="muted" style={{ marginBottom: 0 }}>
              {locale === "zh-CN"
                ? "默认创建托管副本并保存到 .papercreator；原文件只作为来源记录，移动或删除原文件不会破坏工作台。代码导入会排除 node_modules、虚拟环境、构建目录及 .env 密钥。"
                : "A managed copy is stored under .papercreator. The original path is provenance only, so moving the original does not break the workbench. Code imports exclude dependencies, build folders and .env secrets."}
            </p>
          </div>
          {category.kind !== "idea" && (
            <div className="field">
              <label>{locale === "zh-CN" ? "来源" : "Source"}</label>
              <div className="row">
                <input className="grow mono" value={sourcePath} readOnly />
                <button className="btn" onClick={() => void chooseFile()}>
                  {locale === "zh-CN" ? "选择文件" : "Choose file"}
                </button>
                {supportsFolder && (
                  <button className="btn" onClick={() => void chooseFolder()}>
                    {locale === "zh-CN" ? "选择目录" : "Choose folder"}
                  </button>
                )}
              </div>
            </div>
          )}
          <div className="field">
            <label>
              {locale === "zh-CN" ? "名称" : "Title"}
              {category.kind === "idea" ? " *" : ""}
            </label>
            <input
              autoFocus={category.kind === "idea"}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder={
                category.kind === "idea"
                  ? locale === "zh-CN" ? "一句话概括研究问题" : "One-line research question"
                  : locale === "zh-CN" ? "留空则使用文件名" : "Leave blank to use the filename"
              }
            />
          </div>
          <div className="field">
            <label>{locale === "zh-CN" ? "说明 / 内容" : "Description / content"}</label>
            <textarea
              rows={5}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder={
                category.kind === "idea"
                  ? locale === "zh-CN" ? "背景、问题、方法设想、预期贡献和疑问…" : "Context, problem, possible method, intended contribution and open questions…"
                  : locale === "zh-CN" ? "说明它与研究的关系、版本或使用限制。" : "How it relates to the research, its version or usage constraints."
              }
            />
          </div>
          {project && (
            <div className="hint">
              {locale === "zh-CN"
                ? `同时关联到当前论文项目：${project.title}`
                : `Also linked to the open paper project: ${project.title}`}
            </div>
          )}
          {activeJobId && (
            <div className="card" aria-live="polite">
              <div className="row" style={{ marginBottom: 8 }}>
                <strong className="grow">
                  {locale === "zh-CN" ? "安全导入进度" : "Safe import progress"}
                </strong>
                <span className="mono">{Math.round(progress * 100)}%</span>
              </div>
              <div className="progress" style={{ marginBottom: 8 }}>
                <div style={{ width: `${Math.round(progress * 100)}%` }} />
              </div>
              <div className="muted">{progressMessage}</div>
              <div className="dim mono" style={{ marginTop: 5 }}>{activeJobId}</div>
              <p className="dim" style={{ marginBottom: 0 }}>
                {locale === "zh-CN"
                  ? "完成前不会出现 ready 资源；取消或失败会清理 .partial 副本。关闭窗口只会转入后台，可在 Output → Jobs 查看。"
                  : "No ready resource is exposed before commit. Cancellation or failure cleans the .partial copy. Closing only detaches; follow it in Output → Jobs."}
              </p>
            </div>
          )}
        </div>
        <footer>
          {activeJobId ? (
            <>
              <button className="btn danger" onClick={() => void cancelImport()} disabled={cancelling}>
                {cancelling
                  ? locale === "zh-CN" ? "正在取消…" : "Cancelling…"
                  : locale === "zh-CN" ? "取消导入" : "Cancel import"}
              </button>
              <button className="btn" onClick={closeOrDetach}>
                {locale === "zh-CN" ? "转入后台" : "Run in background"}
              </button>
            </>
          ) : (
            <button className="btn" onClick={onClose} disabled={busy}>
              {locale === "zh-CN" ? "取消" : "Cancel"}
            </button>
          )}
          <button className="btn primary" onClick={() => void submit()} disabled={busy}>
            {busy
              ? sourceIsDirectory
                ? locale === "zh-CN" ? "正在安全导入…" : "Importing safely…"
                : locale === "zh-CN" ? "正在复制与登记…" : "Copying and registering…"
              : category.kind === "idea"
                ? locale === "zh-CN" ? "保存 Idea" : "Save idea"
                : locale === "zh-CN" ? "导入托管副本" : "Import managed copy"}
          </button>
        </footer>
      </div>
    </div>
  );
}

function fileFilters(kind: WorkbenchResourceKind) {
  if (kind === "reference_paper") {
    return [
      { name: "Papers and bibliographies", extensions: ["pdf", "bib", "ris", "csv", "json"] },
      { name: "All files", extensions: ["*"] },
    ];
  }
  if (kind === "own_paper") {
    return [
      { name: "Manuscripts", extensions: ["pdf", "docx", "md", "markdown", "txt", "tex", "bib", "ris"] },
      { name: "All files", extensions: ["*"] },
    ];
  }
  return [{ name: "All files", extensions: ["*"] }];
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

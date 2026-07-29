/**
 * Export view: Markdown, LaTeX, DOCX, BibTeX, bundle, PDF, Overleaf.
 *
 * What each format can and cannot do is stated up front, because the differences
 * are real and matter: Word has no citation engine so markers become numbers,
 * LaTeX needs xelatex for Chinese, and a local PDF build needs a TeX
 * installation. Finding that out after an export is worse than reading it before.
 */

import { useEffect, useState } from "react";

import * as endpoints from "../api/endpoints";
import { useStore } from "../state/store";
import type { Project } from "../api/types";

/**
 * Guard wrapper. Splitting it out means the body below receives a non-null
 * project by construction, instead of asserting it inside every async callback
 * (where TypeScript cannot narrow a captured value).
 */
export function ExportView() {
  const project = useStore((s) => s.project);
  if (!project) return null;
  return <ExportBody project={project} />;
}

function ExportBody({ project }: { project: Project }) {
  const stats = useStore((s) => s.stats);
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);

  const [capabilities, setCapabilities] = useState<Record<string, any> | null>(null);
  const [files, setFiles] = useState<any[]>([]);
  const [busy, setBusy] = useState("");
  const [documentClass, setDocumentClass] = useState("article");
  const [language, setLanguage] = useState<"primary" | "paired">("primary");
  const [citedOnly, setCitedOnly] = useState(true);
  const [overleaf, setOverleaf] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    void endpoints.exports.capabilities().then(setCapabilities).catch(() => undefined);
    void endpoints.exports
      .files(project.id)
      .then((result) => setFiles(result.items))
      .catch(() => setFiles([]));
    void endpoints.exports.overleafStatus(project.id).then(setOverleaf).catch(() => undefined);
  }, [project.id]);

  async function run(format: string) {
    setBusy(format);
    try {
      const result = await endpoints.exports.run(project.id, {
        format,
        language,
        document_class: documentClass,
        cited_only: citedOnly,
      });
      const warnings = (result.warnings as string[]) ?? [];
      notify({
        kind: warnings.length ? "warning" : "success",
        message: locale === "zh-CN" ? `${format} 已写入 ${String(result.path).split(/[\\/]/).pop()}` : `${format} written to ${String(result.path).split(/[\\/]/).pop()}`,
        detail: warnings.join(" · ") || String(result.path),
      });
      const listing = await endpoints.exports.files(project.id);
      setFiles(listing.items);
      // Reveal it: on a desktop app the next action is almost always to open it.
      if (result.path && window.papercreator) {
        void window.papercreator.shell.showItem(String(result.path));
      }
    } catch (error) {
      reportError(error, `exporting ${format}`);
    } finally {
      setBusy("");
    }
  }

  const canPdf = Boolean(capabilities?.can_build_pdf);
  const engines = (capabilities?.latex_engines ?? {}) as Record<string, boolean>;

  return (
    <div className="view">
      <h1>{locale === "zh-CN" ? "导出" : "Export"}</h1>
      <p className="sub">
        {locale === "zh-CN"
          ? "所有格式都不依赖外部程序：LaTeX 项目和 Word 文档都由内置写出器生成。安装 Pandoc 或 TeX 只会提升效果。"
          : "No external program is required: the LaTeX project and the Word document are both produced by built-in writers. Pandoc and TeX only improve the result."}
      </p>

      {stats && stats.words === 0 && (
        <p className="warn-text">
          ⚠{" "}
          {locale === "zh-CN"
            ? "手稿目前没有任何正文内容，导出结果会是空的。"
            : "The manuscript has no body text yet, so the export will be empty."}
        </p>
      )}

      <div className="card">
        <div className="row wrap">
          <div className="field" style={{ width: 210, marginBottom: 0 }}>
            <label>{locale === "zh-CN" ? "语言版本" : "Language version"}</label>
            <select
              value={language}
              onChange={(event) => setLanguage(event.target.value as typeof language)}
            >
              <option value="primary">
                {locale === "zh-CN" ? `主语言（${project.language}）` : `primary (${project.language})`}
              </option>
              <option value="paired" disabled={!project.bilingual}>
                {locale === "zh-CN" ? "对照语言" : "paired language"}
              </option>
            </select>
          </div>
          <div className="field" style={{ width: 260, marginBottom: 0 }}>
            <label>{locale === "zh-CN" ? "LaTeX 文档类" : "LaTeX document class"}</label>
            <select
              value={documentClass}
              onChange={(event) => setDocumentClass(event.target.value)}
            >
              {((capabilities?.document_classes ?? []) as any[]).map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.name}
                </option>
              ))}
            </select>
            {((capabilities?.document_classes ?? []) as any[]).find(
              (entry) => entry.id === documentClass,
            )?.note && (
              <span className="hint">
                {
                  ((capabilities?.document_classes ?? []) as any[]).find(
                    (entry) => entry.id === documentClass,
                  ).note
                }
              </span>
            )}
          </div>
          <label className="row" style={{ gap: 6, marginTop: 18 }}>
            <input
              type="checkbox"
              checked={citedOnly}
              onChange={(event) => setCitedOnly(event.target.checked)}
            />
            {locale === "zh-CN" ? "参考文献只含已引用" : "Bibliography: cited papers only"}
          </label>
        </div>
      </div>

      <div className="cards">
        <ExportCard
          title={locale === "zh-CN" ? "Markdown" : "Markdown"}
          note={
            locale === "zh-CN"
              ? "引用转为编号并附参考文献列表。适合快速阅读与分享。"
              : "Citations become numbers with a reference list. Best for reading and sharing."
          }
          busy={busy === "markdown"}
          onRun={() => void run("markdown")}
        />
        <ExportCard
          title="LaTeX"
          note={
            locale === "zh-CN"
              ? "完整可编译项目：main.tex、sections/、references.bib，可直接上传 Overleaf。"
              : "A complete compilable project: main.tex, sections/, references.bib — ready for Overleaf."
          }
          busy={busy === "latex"}
          onRun={() => void run("latex")}
          extra={
            project.language === "zh"
              ? locale === "zh-CN"
                ? "中文手稿会自动使用 xelatex + ctex"
                : "Chinese manuscripts automatically use xelatex + ctex"
              : ""
          }
        />
        <ExportCard
          title={locale === "zh-CN" ? "Word (.docx)" : "Word (.docx)"}
          note={
            capabilities?.pandoc
              ? locale === "zh-CN"
                ? "检测到 Pandoc，将使用它以获得更高保真度。"
                : "Pandoc detected — it will be used for higher fidelity."
              : locale === "zh-CN"
                ? "使用内置写出器（标题、列表、表格、强调均支持）。安装 Pandoc 可进一步提升。"
                : "Uses the built-in writer (headings, lists, tables, emphasis). Install Pandoc for more."
          }
          busy={busy === "docx"}
          onRun={() => void run("docx")}
        />
        <ExportCard
          title="BibTeX"
          note={
            locale === "zh-CN"
              ? "只导出参考文献，方便粘进已有的 LaTeX 项目。"
              : "The bibliography alone, for pasting into an existing LaTeX project."
          }
          busy={busy === "bibtex"}
          onRun={() => void run("bibtex")}
        />
        <ExportCard
          title={locale === "zh-CN" ? "打包 (.zip)" : "Bundle (.zip)"}
          note={
            locale === "zh-CN"
              ? "以上格式打包在一起，方便交给合作者。"
              : "All of the above in one archive, for handing to a co-author."
          }
          busy={busy === "zip"}
          onRun={() => void run("zip")}
        />
        <ExportCard
          title="PDF"
          note={
            canPdf
              ? locale === "zh-CN"
                ? `本机可编译（${Object.keys(engines).filter((k) => engines[k]).join(", ")}）`
                : `Can build locally with ${Object.keys(engines).filter((k) => engines[k]).join(", ")}`
              : locale === "zh-CN"
                ? "本机未安装 TeX。仍可导出 LaTeX 项目并上传 Overleaf 编译。"
                : "No TeX installation found. Export the LaTeX project and compile it on Overleaf instead."
          }
          busy={busy === "pdf"}
          disabled={!canPdf}
          onRun={async () => {
            setBusy("pdf");
            try {
              const result = await endpoints.exports.pdf(project.id, documentClass);
              notify({ kind: "success", message: locale === "zh-CN" ? "PDF 已生成" : "PDF built", detail: String(result.path) });
              if (window.papercreator) {
                void window.papercreator.shell.openPath(String(result.path));
              }
            } catch (error) {
              reportError(error, "building the PDF");
            } finally {
              setBusy("");
            }
          }}
        />
      </div>

      <h2>Overleaf</h2>
      <div className="card">
        {overleaf?.git_configured ? (
          <>
            <p className="muted">
              {locale === "zh-CN"
                ? "已配置 git 桥接，可双向同步。推送只会替换 PaperCreator 管理的文件，合作者在 Overleaf 添加的内容会保留。"
                : "The git bridge is configured, so sync works both ways. A push replaces only the files PaperCreator manages — a co-author's additions in Overleaf survive."}
            </p>
            <div className="row wrap">
              <button
                className="btn primary"
                disabled={busy === "push"}
                onClick={async () => {
                  setBusy("push");
                  try {
                    const result = await endpoints.exports.overleafPush(project.id, {
                      document_class: documentClass,
                      language,
                    });
                    notify({
                      kind: result.pushed ? "success" : "info",
                      message: result.pushed
                        ? locale === "zh-CN" ? "已推送到 Overleaf" : "Pushed to Overleaf"
                        : String(result.reason ?? "nothing to push"),
                      detail: (result.warnings as string[])?.join(" · "),
                    });
                  } catch (error) {
                    reportError(error, "pushing to Overleaf");
                  } finally {
                    setBusy("");
                  }
                }}
              >
                {locale === "zh-CN" ? "推送到 Overleaf" : "Push to Overleaf"}
              </button>
              <button
                className="btn"
                disabled={busy === "pull"}
                onClick={async () => {
                  setBusy("pull");
                  try {
                    const result = await endpoints.exports.overleafPull(project.id, false);
                    notify({
                      kind: "info",
                      message: locale === "zh-CN" ? `已获取 ${(result.tex_files as string[]).length} 个 .tex 文件` : `Fetched ${(result.tex_files as string[]).length} .tex files`,
                      detail: (result.warnings as string[])?.join(" · "),
                    });
                  } catch (error) {
                    reportError(error, "pulling from Overleaf");
                  } finally {
                    setBusy("");
                  }
                }}
              >
                {locale === "zh-CN" ? "拉取合作者改动" : "Fetch co-author changes"}
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="muted">
              {locale === "zh-CN"
                ? "Overleaf 的 git 访问是付费功能。未配置时可以用 zip 上传，任何账号都能用。"
                : "Overleaf's git access is a paid feature. Without it, use the zip upload — that works with any account."}
            </p>
            <button
              className="btn primary"
              disabled={busy === "overleafzip"}
              onClick={async () => {
                setBusy("overleafzip");
                try {
                  const result = await endpoints.exports.overleafZip(project.id, documentClass);
                  notify({
                    kind: "success",
                    message: locale === "zh-CN" ? "Overleaf 上传包已就绪" : "Overleaf archive ready",
                    detail: (result.instructions as string[]).join(" → "),
                  });
                  if (window.papercreator) {
                    void window.papercreator.shell.showItem(String(result.path));
                  }
                } catch (error) {
                  reportError(error, "building the Overleaf archive");
                } finally {
                  setBusy("");
                }
              }}
            >
              {locale === "zh-CN" ? "生成 Overleaf 上传包" : "Build Overleaf upload archive"}
            </button>
            <button
              className="btn"
              style={{ marginLeft: 8 }}
              onClick={() => useStore.getState().setView("settings")}
            >
              {locale === "zh-CN" ? "配置 git 桥接" : "Configure the git bridge"}
            </button>
          </>
        )}
      </div>

      {files.length > 0 && (
        <>
          <h2>{locale === "zh-CN" ? "已导出的文件" : "Exported files"}</h2>
          <table className="data">
            <thead>
              <tr>
                <th>{locale === "zh-CN" ? "文件" : "File"}</th>
                <th className="num">{locale === "zh-CN" ? "大小" : "Size"}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {files.map((file) => (
                <tr key={String(file.path)}>
                  <td className="mono">{String(file.relative)}</td>
                  <td className="num">{Math.ceil(Number(file.bytes) / 1024)} KB</td>
                  <td>
                    <button
                      className="btn sm"
                      onClick={() =>
                        void window.papercreator?.shell.showItem(String(file.path))
                      }
                    >
                      {locale === "zh-CN" ? "在资源管理器中显示" : "Reveal"}
                    </button>{" "}
                    <button
                      className="btn sm"
                      onClick={() => void window.papercreator?.shell.openPath(String(file.path))}
                    >
                      {locale === "zh-CN" ? "打开" : "Open"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function ExportCard({
  title,
  note,
  busy,
  disabled,
  extra,
  onRun,
}: {
  title: string;
  note: string;
  busy: boolean;
  disabled?: boolean;
  extra?: string;
  onRun: () => void;
}) {
  const locale = useStore((s) => s.locale);
  return (
    <div className="card">
      <h3>{title}</h3>
      <p className="muted" style={{ fontSize: "var(--fs-sm)", minHeight: 48 }}>
        {note}
      </p>
      {extra && (
        <p className="dim" style={{ fontSize: "var(--fs-xs)" }}>
          {extra}
        </p>
      )}
      <button className="btn primary" onClick={onRun} disabled={busy || disabled}>
        {busy
          ? locale === "zh-CN" ? "导出中…" : "Exporting…"
          : locale === "zh-CN" ? "导出" : "Export"}
      </button>
    </div>
  );
}

/**
 * Library view: browse, triage, import, add your own idea.
 *
 * Triage matters as much as browsing: a 300-paper search result is only useful
 * once rated and read-marked, so rating and status are editable inline rather
 * than behind a detail panel.
 */

import { useEffect, useState } from "react";

import * as endpoints from "../api/endpoints";
import { useStore } from "../state/store";
import type { Paper } from "../api/types";

export function LibraryView() {
  const papers = useStore((s) => s.libraryPapers);
  const total = useStore((s) => s.libraryTotal);
  const loadLibrary = useStore((s) => s.loadLibrary);
  const selected = useStore((s) => s.selectedPaperIds);
  const toggleSelected = useStore((s) => s.togglePaperSelected);
  const clearSelection = useStore((s) => s.clearSelection);
  const project = useStore((s) => s.project);
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);

  const [text, setText] = useState("");
  const [scope, setScope] = useState<"project" | "all">(project ? "project" : "all");
  const [origin, setOrigin] = useState("");
  const [readStatus, setReadStatus] = useState("");
  const [minRating, setMinRating] = useState(0);
  const [sort, setSort] = useState("updated");
  const [addingIdea, setAddingIdea] = useState(false);
  const [duplicates, setDuplicates] = useState<Record<string, any> | null>(null);

  function reload() {
    void loadLibrary({
      text,
      project_id: scope === "project" && project ? project.id : "",
      origin,
      read_status: readStatus,
      min_rating: minRating,
      sort,
      limit: 300,
    });
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, origin, readStatus, minRating, sort, project?.id]);

  async function importFile() {
    const path = await window.papercreator?.dialog.openFile([
      { name: "Bibliography", extensions: ["bib", "ris", "csv", "json"] },
    ]);
    if (!path) return;
    try {
      const result = await endpoints.workbench.importResource({
        kind: "reference_paper",
        source_path: path,
        project_id: project?.id ?? "",
      });
      notify({
        kind: "success",
        message: locale === "zh-CN" ? `已导入 ${result.papers.length} 条记录` : `Imported ${result.papers.length} records`,
        detail:
          result.warnings?.join("; ") ||
          `Managed copy: ${result.resource.managed_path}`,
      });
      // Project paper counts drive the Landscape build controls. Refresh them
      // immediately instead of requiring the user to reopen the project.
      if (project) await useStore.getState().reloadDocument();
      reload();
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "导入文献文件" : "importing the file");
    }
  }

  return (
    <div className="view">
      <div className="row">
        <div className="grow">
          <h1>{locale === "zh-CN" ? "文献库" : "Paper library"}</h1>
          <p className="sub">
            {locale === "zh-CN"
              ? "全局文献库，项目通过集合引用其中的论文。删除会影响所有项目。"
              : "The library is global; projects reference papers through collections. Deleting affects every project."}
          </p>
        </div>
        <button className="btn" onClick={() => void importFile()}>
          {locale === "zh-CN" ? "导入 .bib/.ris/.csv" : "Import .bib/.ris/.csv"}
        </button>
        <button className="btn primary" onClick={() => setAddingIdea(true)}>
          {locale === "zh-CN" ? "添加我的想法/论文" : "Add my idea or paper"}
        </button>
      </div>

      {addingIdea && <AddPaperModal onClose={() => setAddingIdea(false)} onSaved={reload} />}

      <div className="card">
        <div className="row wrap">
          <input
            className="grow"
            value={text}
            placeholder={locale === "zh-CN" ? "搜索标题或摘要…" : "Search title or abstract…"}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") reload();
            }}
            style={{ minWidth: 220 }}
          />
          <select value={scope} onChange={(event) => setScope(event.target.value as typeof scope)}>
            <option value="project" disabled={!project}>
              {locale === "zh-CN" ? "当前项目" : "This project"}
            </option>
            <option value="all">{locale === "zh-CN" ? "全部文献" : "Entire library"}</option>
          </select>
          <select value={origin} onChange={(event) => setOrigin(event.target.value)}>
            <option value="">{locale === "zh-CN" ? "全部来源" : "any origin"}</option>
            <option value="retrieved">{locale === "zh-CN" ? "检索所得" : "retrieved"}</option>
            <option value="manual">{locale === "zh-CN" ? "手工添加" : "manual"}</option>
            <option value="idea">{locale === "zh-CN" ? "我的想法" : "my idea"}</option>
            <option value="own_paper">{locale === "zh-CN" ? "我的论文" : "my paper"}</option>
          </select>
          <select value={readStatus} onChange={(event) => setReadStatus(event.target.value)}>
            <option value="">{locale === "zh-CN" ? "全部状态" : "any status"}</option>
            <option value="unread">{locale === "zh-CN" ? "未读" : "unread"}</option>
            <option value="skimmed">{locale === "zh-CN" ? "略读" : "skimmed"}</option>
            <option value="read">{locale === "zh-CN" ? "已读" : "read"}</option>
          </select>
          <select
            value={minRating}
            onChange={(event) => setMinRating(Number(event.target.value))}
          >
            <option value={0}>{locale === "zh-CN" ? "全部评分" : "any rating"}</option>
            {[1, 2, 3, 4, 5].map((value) => (
              <option key={value} value={value}>
                ≥ {"★".repeat(value)}
              </option>
            ))}
          </select>
          <select value={sort} onChange={(event) => setSort(event.target.value)}>
            <option value="updated">{locale === "zh-CN" ? "最近更新" : "recently updated"}</option>
            <option value="relevance">{locale === "zh-CN" ? "相关度" : "relevance"}</option>
            <option value="year">{locale === "zh-CN" ? "年份" : "year"}</option>
            <option value="citations">{locale === "zh-CN" ? "引用数" : "citations"}</option>
            <option value="rating">{locale === "zh-CN" ? "评分" : "rating"}</option>
            <option value="title">{locale === "zh-CN" ? "标题" : "title"}</option>
          </select>
          <button className="btn" onClick={reload}>
            {locale === "zh-CN" ? "刷新" : "Refresh"}
          </button>
        </div>

        {selected.length > 0 && (
          <div className="row wrap" style={{ marginTop: 10 }}>
            <span className="chip on">
              {selected.length} {locale === "zh-CN" ? "已选" : "selected"}
            </span>
            {project && (
              <button
                className="btn sm"
                onClick={async () => {
                  try {
                    const collections = await endpoints.projects.collections(project.id);
                    const target =
                      collections.items.find((c: any) => c.name === "default") ??
                      collections.items[0];
                    if (!target) return;
                    await endpoints.projects.addPapers(project.id, target.id, selected);
                    notify({
                      kind: "success",
                      message: locale === "zh-CN" ? "已加入当前项目" : "Added to the project",
                    });
                    clearSelection();
                    reload();
                  } catch (error) {
                    reportError(error, locale === "zh-CN" ? "加入当前项目" : "adding to the project");
                  }
                }}
              >
                {locale === "zh-CN" ? "加入当前项目" : "Add to project"}
              </button>
            )}
            <button
              className="btn sm"
              onClick={async () => {
                const { job_id } = await endpoints.library.downloadPdfs(selected);
                notify({
                  kind: "info",
                  message: locale === "zh-CN" ? "正在下载开放获取 PDF" : "Downloading open-access PDFs",
                  detail: locale === "zh-CN"
                    ? `任务 ${job_id.slice(0, 8)} — 将跳过付费墙论文`
                    : `job ${job_id.slice(0, 8)} — paywalled papers are skipped`,
                });
              }}
            >
              {locale === "zh-CN" ? "下载开放获取 PDF" : "Download OA PDFs"}
            </button>
            <button
              className="btn sm danger"
              onClick={async () => {
                if (
                  !window.confirm(
                    `Remove ${selected.length} paper(s) from the library entirely? ` +
                      `This affects every project.`,
                  )
                )
                  return;
                await endpoints.library.removeMany(selected);
                clearSelection();
                reload();
              }}
            >
              {locale === "zh-CN" ? "删除" : "Delete"}
            </button>
            <button className="btn sm" onClick={clearSelection}>
              {locale === "zh-CN" ? "取消选择" : "Clear"}
            </button>
          </div>
        )}

        <div className="row" style={{ marginTop: 10 }}>
          <span className="dim">
            {papers.length} {locale === "zh-CN" ? "显示" : "shown"} / {total}{" "}
            {locale === "zh-CN" ? "匹配" : "matching"}
          </span>
          <div className="grow" />
          <button
            className="btn sm"
            onClick={async () => {
              try {
                setDuplicates(
                  await endpoints.library.duplicates(
                    scope === "project" && project ? project.id : "",
                  ),
                );
              } catch (error) {
                reportError(error, "scanning for duplicates");
              }
            }}
          >
            {locale === "zh-CN" ? "查找重复" : "Find duplicates"}
          </button>
        </div>
      </div>

      {duplicates && (
        <DuplicatePanel
          data={duplicates}
          onClose={() => setDuplicates(null)}
          onMerged={reload}
        />
      )}

      {papers.length === 0 ? (
        <div className="empty">
          <div className="big">❑</div>
          <div>{locale === "zh-CN" ? "没有匹配的文献" : "No matching papers"}</div>
          <p className="dim">
            {locale === "zh-CN"
              ? "先执行一次检索，或导入现有的 .bib 文件。"
              : "Run a search, or import an existing .bib file."}
          </p>
        </div>
      ) : (
        <table className="data">
          <thead>
            <tr>
              <th style={{ width: 26 }} />
              <th>{locale === "zh-CN" ? "标题" : "Title"}</th>
              <th style={{ width: 54 }}>{locale === "zh-CN" ? "年份" : "Year"}</th>
              <th style={{ width: 58 }} className="num">
                {locale === "zh-CN" ? "引用" : "Cites"}
              </th>
              <th style={{ width: 86 }}>{locale === "zh-CN" ? "评分" : "Rating"}</th>
              <th style={{ width: 92 }}>{locale === "zh-CN" ? "状态" : "Status"}</th>
            </tr>
          </thead>
          <tbody>
            {papers.map((paper) => (
              <LibraryRow
                key={paper.id}
                paper={paper}
                selected={selected.includes(paper.id)}
                onToggle={() => toggleSelected(paper.id)}
                onChanged={reload}
              />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function LibraryRow({
  paper,
  selected,
  onToggle,
  onChanged,
}: {
  paper: Paper;
  selected: boolean;
  onToggle: () => void;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [notes, setNotes] = useState(paper.notes);
  const locale = useStore((s) => s.locale);

  async function patch(fields: Record<string, unknown>) {
    try {
      await endpoints.library.update(paper.id, fields);
      onChanged();
    } catch (error) {
      useStore.getState().reportError(error, locale === "zh-CN" ? "更新文献" : "updating the paper");
    }
  }

  return (
    <>
      <tr className={selected ? "selected" : ""}>
        <td>
          <input type="checkbox" checked={selected} onChange={onToggle} />
        </td>
        <td onClick={() => setOpen(!open)} style={{ cursor: "pointer" }}>
          <div>
            {paper.origin === "idea" || paper.origin === "own_paper" ? (
              <span className="chip on" style={{ marginRight: 6 }}>
                {paper.origin === "idea"
                  ? locale === "zh-CN" ? "想法" : "idea"
                  : locale === "zh-CN" ? "我的论文" : "mine"}
              </span>
            ) : null}
            {paper.title}
          </div>
          <div className="dim" style={{ fontSize: "var(--fs-xs)" }}>
            {paper.authors.slice(0, 3).map((a) => a.name).join(", ")}
            {paper.authors.length > 3 ? " et al." : ""}
            {paper.venue ? ` · ${paper.venue}` : ""}
            {paper.pdf_path ? " · PDF" : paper.is_open_access ? " · OA" : ""}
          </div>
        </td>
        <td>{paper.year ?? "—"}</td>
        <td className="num">{paper.citation_count}</td>
        <td>
          {[1, 2, 3, 4, 5].map((value) => (
            <button
              key={value}
              onClick={() => void patch({ rating: paper.rating === value ? 0 : value })}
              title={locale === "zh-CN" ? `${value} 星` : `${value} star${value > 1 ? "s" : ""}`}
              style={{
                color: value <= paper.rating ? "var(--warn)" : "var(--fg-dim)",
                padding: "0 1px",
              }}
            >
              ★
            </button>
          ))}
        </td>
        <td>
          <select
            value={paper.read_status}
            onChange={(event) => void patch({ read_status: event.target.value })}
            style={{ padding: "1px 4px", fontSize: "var(--fs-xs)" }}
          >
            <option value="unread">{locale === "zh-CN" ? "未读" : "unread"}</option>
            <option value="skimmed">{locale === "zh-CN" ? "略读" : "skimmed"}</option>
            <option value="read">{locale === "zh-CN" ? "已读" : "read"}</option>
          </select>
        </td>
      </tr>
      {open && (
        <tr>
          <td />
          <td colSpan={5} style={{ paddingBottom: 14 }}>
            {paper.abstract ? (
              <p style={{ userSelect: "text" }}>{paper.abstract}</p>
            ) : (
              <p className="dim">{locale === "zh-CN" ? "（无摘要）" : "(no abstract)"}</p>
            )}
            <div className="field" style={{ maxWidth: 620 }}>
              <label>{locale === "zh-CN" ? "我的笔记" : "My notes"}</label>
              <textarea
                rows={3}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                onBlur={() => {
                  if (notes !== paper.notes) void patch({ notes });
                }}
              />
            </div>
            <div className="row wrap" style={{ gap: 5 }}>
              {paper.doi && <span className="chip mono">doi:{paper.doi}</span>}
              {paper.arxiv_id && <span className="chip mono">arXiv:{paper.arxiv_id}</span>}
              {paper.source_providers.map((provider) => (
                <span key={provider} className="chip">
                  {provider}
                </span>
              ))}
              {paper.keywords.slice(0, 6).map((keyword) => (
                <span key={keyword} className="chip">
                  {keyword}
                </span>
              ))}
              {paper.url && (
                <button
                  className="btn sm"
                  onClick={() => void window.papercreator?.shell.openExternal(paper.url)}
                >
                  {locale === "zh-CN" ? "打开来源" : "open source"}
                </button>
              )}
              {paper.pdf_path && (
                <button
                  className="btn sm"
                  onClick={() => void window.papercreator?.shell.openPath(paper.pdf_path)}
                >
                  {locale === "zh-CN" ? "打开 PDF" : "open PDF"}
                </button>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function AddPaperModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const project = useStore((s) => s.project);
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const [tab, setTab] = useState<"idea" | "identifier">("idea");
  const [busy, setBusy] = useState(false);
  const [identifier, setIdentifier] = useState("");
  const [form, setForm] = useState({
    title: "",
    abstract: "",
    authors: "",
    year: "" as number | "",
    venue: "",
    origin: "idea",
  });

  async function saveIdea() {
    setBusy(true);
    try {
      await endpoints.workbench.importResource({
        kind: form.origin === "idea" ? "idea" : "own_paper",
        title: form.title,
        content: form.abstract,
        authors: form.authors.split(",").map((a) => a.trim()).filter(Boolean),
        year: form.year || null,
        venue: form.venue,
        project_id: project?.id ?? "",
      });
      notify({ kind: "success", message: locale === "zh-CN" ? "已加入文献库" : "Added to the library" });
      onSaved();
      onClose();
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "添加文献" : "adding the entry");
    } finally {
      setBusy(false);
    }
  }

  async function resolve() {
    setBusy(true);
    try {
      const result = await endpoints.search.resolve(identifier, project?.id ?? "");
      notify({
        kind: "success",
        message: locale === "zh-CN" ? `已解析：${result.paper.title}` : `Resolved: ${result.paper.title}`,
        detail: locale === "zh-CN"
          ? `来源：${result.paper.source_providers.join(", ")}`
          : `from ${result.paper.source_providers.join(", ")}`,
      });
      onSaved();
      onClose();
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "解析标识符" : "resolving the identifier");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <header>
          <span>{locale === "zh-CN" ? "添加文献" : "Add an entry"}</span>
          <button className="btn icon sm" onClick={onClose} aria-label={locale === "zh-CN" ? "关闭添加文献" : "Close add entry"}>
            ✕
          </button>
        </header>
        <div className="modal-body">
          <div className="row" style={{ marginBottom: 14 }}>
            <button
              className={`btn${tab === "idea" ? " primary" : ""}`}
              onClick={() => setTab("idea")}
            >
              {locale === "zh-CN" ? "我的想法 / 论文" : "My idea or paper"}
            </button>
            <button
              className={`btn${tab === "identifier" ? " primary" : ""}`}
              onClick={() => setTab("identifier")}
            >
              {locale === "zh-CN" ? "按 DOI / arXiv 查找" : "By DOI / arXiv id"}
            </button>
          </div>

          {tab === "identifier" ? (
            <>
              <div className="field">
                <label>{locale === "zh-CN" ? "标识符" : "Identifier"}</label>
                <input
                  autoFocus
                  value={identifier}
                  onChange={(event) => setIdentifier(event.target.value)}
                  placeholder="10.1109/tnn.2008.2005605  ·  2301.01234  ·  35271234"
                />
                <span className="hint">
                  {locale === "zh-CN"
                    ? "会依次查询多个数据库并合并结果，得到最完整的记录。"
                    : "Queried across several databases and merged, so the record is as complete as possible."}
                </span>
              </div>
              <button
                className="btn primary"
                onClick={() => void resolve()}
                disabled={busy || !identifier.trim()}
              >
                {busy ? "Resolving…" : locale === "zh-CN" ? "查找并添加" : "Resolve and add"}
              </button>
            </>
          ) : (
            <>
              <div className="field">
                <label>{locale === "zh-CN" ? "类型" : "Kind"}</label>
                <select
                  value={form.origin}
                  onChange={(event) => setForm({ ...form, origin: event.target.value })}
                >
                  <option value="idea">
                    {locale === "zh-CN" ? "我的想法（尚未成文）" : "My idea (not yet written)"}
                  </option>
                  <option value="own_paper">
                    {locale === "zh-CN" ? "我自己的论文" : "My own paper"}
                  </option>
                  <option value="manual">
                    {locale === "zh-CN" ? "手动录入的他人论文" : "Someone else's paper, entered by hand"}
                  </option>
                </select>
                <span className="hint">
                  {locale === "zh-CN"
                    ? "「想法」和「我的论文」会在研究图谱中以高亮点显示。"
                    : "Ideas and your own papers are highlighted as seed points on the landscape."}
                </span>
              </div>
              <div className="field">
                <label>{locale === "zh-CN" ? "标题" : "Title"}</label>
                <input
                  autoFocus
                  value={form.title}
                  onChange={(event) => setForm({ ...form, title: event.target.value })}
                />
              </div>
              <div className="field">
                <label>{locale === "zh-CN" ? "摘要 / 描述" : "Abstract / description"}</label>
                <textarea
                  rows={5}
                  value={form.abstract}
                  onChange={(event) => setForm({ ...form, abstract: event.target.value })}
                />
                <span className="hint">
                  {locale === "zh-CN"
                    ? "图谱定位主要依据这段文字，越具体越准。"
                    : "Placement on the landscape is computed from this text — the more specific, the better."}
                </span>
              </div>
              <div className="row">
                <div className="field grow">
                  <label>{locale === "zh-CN" ? "作者（逗号分隔）" : "Authors (comma separated)"}</label>
                  <input
                    value={form.authors}
                    onChange={(event) => setForm({ ...form, authors: event.target.value })}
                  />
                </div>
                <div className="field" style={{ width: 110 }}>
                  <label>{locale === "zh-CN" ? "年份" : "Year"}</label>
                  <input
                    type="number"
                    value={form.year}
                    onChange={(event) =>
                      setForm({ ...form, year: Number(event.target.value) || "" })
                    }
                  />
                </div>
              </div>
              <button
                className="btn primary"
                onClick={() => void saveIdea()}
                disabled={busy || (!form.title.trim() && !form.abstract.trim())}
              >
                {busy ? "Saving…" : locale === "zh-CN" ? "添加" : "Add"}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function DuplicatePanel({
  data,
  onClose,
  onMerged,
}: {
  data: Record<string, any>;
  onClose: () => void;
  onMerged: () => void;
}) {
  const locale = useStore((s) => s.locale);
  const groups = (data.groups ?? []) as any[];

  return (
    <div className="card">
      <div className="row">
        <h3 className="grow" style={{ margin: 0 }}>
          {locale === "zh-CN" ? "疑似重复" : "Suspected duplicates"}{" "}
          <span className="dim">
            {groups.length} groups / {data.scanned} scanned
          </span>
        </h3>
        <button className="btn icon sm" onClick={onClose} aria-label={locale === "zh-CN" ? "关闭重复项检查" : "Close duplicate review"}>
          ✕
        </button>
      </div>
      <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
        {locale === "zh-CN"
          ? "合并需要你确认：自动合并一旦判断错误，会静默丢失一篇独立论文。"
          : "Merging is left to you — an incorrect automatic merge would silently lose a distinct paper."}
      </p>
      {groups.length === 0 ? (
        <p className="ok-text">
          {locale === "zh-CN" ? "未发现重复。" : "No duplicates found."}
        </p>
      ) : (
        groups.map((group, index) => (
          <div key={index} className="card" style={{ background: "var(--bg-app)" }}>
            {group.papers.map((paper: any) => (
              <div key={paper.id} className="row" style={{ padding: "2px 0" }}>
                <button
                  className="btn sm"
                  title={locale === "zh-CN" ? "保留这条并将其他记录合并到其中" : "Keep this one and merge the others into it"}
                  onClick={async () => {
                    try {
                      await endpoints.library.merge(
                        paper.id,
                        group.paper_ids.filter((id: string) => id !== paper.id),
                      );
                      useStore.getState().notify({ kind: "success", message: locale === "zh-CN" ? "已合并" : "Merged" });
                      onMerged();
                      onClose();
                    } catch (error) {
                      useStore.getState().reportError(error, locale === "zh-CN" ? "合并文献" : "merging");
                    }
                  }}
                >
                  {locale === "zh-CN" ? "保留这条" : "Keep this"}
                </button>
                <span className="grow truncate">{paper.title}</span>
                <span className="dim">
                  {paper.year ?? "—"} · {paper.citations} {locale === "zh-CN" ? "次引用" : "cites"} · {paper.providers?.join(",")}
                </span>
              </div>
            ))}
          </div>
        ))
      )}
    </div>
  );
}

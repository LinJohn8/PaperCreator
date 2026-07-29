/**
 * Activity bar: the top-level areas, in workflow order.
 *
 * The order is the actual sequence of work - projects, search, library,
 * landscape, editor, agents, versions, export - so the bar doubles as a reminder
 * of the pipeline. Areas that need an open project are disabled with a reason
 * rather than hidden, so the app's capabilities stay discoverable.
 */

import { useStore, type ViewId } from "../state/store";

interface Entry {
  id: ViewId;
  label: string;
  labelZh: string;
  glyph: string;
  needsProject?: boolean;
}

const PRIMARY: Entry[] = [
  { id: "projects", label: "Workbench", labelZh: "工作台", glyph: "▤" },
  { id: "search", label: "Search", labelZh: "检索", glyph: "⌕" },
  { id: "library", label: "Library", labelZh: "文献库", glyph: "❑" },
  { id: "landscape", label: "Landscape", labelZh: "研究图谱", glyph: "◈", needsProject: true },
  { id: "editor", label: "Manuscript", labelZh: "手稿", glyph: "✎", needsProject: true },
  { id: "agents", label: "Agents", labelZh: "智能体", glyph: "◍", needsProject: true },
  { id: "versions", label: "Versions", labelZh: "版本", glyph: "⟲", needsProject: true },
  { id: "export", label: "Export", labelZh: "导出", glyph: "⤓", needsProject: true },
];

const SECONDARY: Entry[] = [
  { id: "skills", label: "Skills", labelZh: "技能", glyph: "◆" },
  { id: "settings", label: "Settings", labelZh: "设置", glyph: "⚙" },
];

export function ActivityBar() {
  const view = useStore((s) => s.view);
  const setView = useStore((s) => s.setView);
  const notify = useStore((s) => s.notify);
  const hasProject = Boolean(useStore((s) => s.activeProjectId));
  const locale = useStore((s) => s.locale);
  const runningJobs = useStore((s) => s.jobs.filter((j) => j.status === "running").length);
  const dirtyCount = useStore((s) => Object.keys(s.dirtySections).length);
  const activeRun = useStore((s) => s.activeRun);

  function badgeFor(id: ViewId): number {
    if (id === "editor") return dirtyCount;
    if (id === "agents") return activeRun?.status === "running" ? 1 : 0;
    if (id === "search") return runningJobs;
    return 0;
  }

  function open(entry: Entry) {
    if (entry.needsProject && !hasProject) {
      notify({
        kind: "info",
        message:
          locale === "zh-CN"
            ? "请先打开一个项目"
            : "Open a project first",
        detail:
          locale === "zh-CN"
            ? `「${entry.labelZh}」需要一个已打开的项目。`
            : `${entry.label} works on an open project.`,
        action: { label: locale === "zh-CN" ? "查看项目" : "Browse projects", view: "projects" },
      });
      return;
    }
    setView(entry.id);
  }

  function render(entry: Entry) {
    const disabled = Boolean(entry.needsProject && !hasProject);
    const badge = badgeFor(entry.id);
    const title = locale === "zh-CN" ? entry.labelZh : entry.label;
    return (
      <button
        key={entry.id}
        className={view === entry.id ? "active" : ""}
        onClick={() => open(entry)}
        title={
          disabled
            ? locale === "zh-CN"
              ? `${title}——需要先打开项目`
              : `${title} — needs an open project`
            : title
        }
        aria-label={title}
        aria-current={view === entry.id}
        style={disabled ? { opacity: 0.35 } : undefined}
      >
        <span style={{ fontSize: 19, lineHeight: 1 }}>{entry.glyph}</span>
        {badge > 0 && <span className="badge">{badge > 99 ? "99+" : badge}</span>}
      </button>
    );
  }

  return (
    <nav className="activitybar" aria-label={locale === "zh-CN" ? "主要功能区" : "Primary areas"}>
      {PRIMARY.map(render)}
      <div className="grow" />
      {SECONDARY.map(render)}
    </nav>
  );
}

/**
 * Title bar: project identity, unsaved state, connection health.
 *
 * Kept deliberately informative rather than decorative - the two things a user
 * needs to know at a glance are which project is open and whether the backend is
 * still there, and both are easy to lose track of in a long session.
 */

import { useEffect, useState, type MouseEvent } from "react";

import { useStore } from "../state/store";
import brandIcon from "../../assets/brand/icon.png";

const MENU_IDS = ["File", "Edit", "View", "Help"] as const;
type MenuId = (typeof MENU_IDS)[number];
const MENU_ZH: Record<MenuId, string> = {
  File: "文件",
  Edit: "编辑",
  View: "视图",
  Help: "帮助",
};

export function TitleBar() {
  const project = useStore((s) => s.project);
  const projects = useStore((s) => s.projects);
  const openProject = useStore((s) => s.openProject);
  const closeProject = useStore((s) => s.closeProject);
  const setView = useStore((s) => s.setView);
  const connection = useStore((s) => s.connection);
  const dirtyCount = useStore((s) => Object.keys(s.dirtySections).length);
  const locale = useStore((s) => s.locale);
  const assistantOpen = useStore((s) => s.assistantOpen);
  const toggleAssistant = useStore((s) => s.toggleAssistant);
  const [appInfo, setAppInfo] = useState({ version: "", platform: "" });

  useEffect(() => {
    void window.papercreator?.appInfo().then((info) =>
      setAppInfo({ version: info.version, platform: info.platform }),
    );
  }, []);

  function openMenu(event: MouseEvent<HTMLButtonElement>, menu: MenuId) {
    const rect = event.currentTarget.getBoundingClientRect();
    void window.papercreator?.menu.popup(menu, { x: rect.left, y: rect.bottom });
  }

  const connectionLabel =
    connection === "open"
      ? locale === "zh-CN" ? "已连接" : "connected"
      : connection === "connecting"
        ? locale === "zh-CN" ? "连接中" : "connecting"
        : locale === "zh-CN" ? "已断开" : "disconnected";

  return (
    <header className={`titlebar platform-${appInfo.platform || "unknown"}`}>
      <div className="titlebar-left">
        <img
          className="titlebar-logo"
          src={brandIcon}
          alt=""
          title={`PaperCreator${appInfo.version ? ` v${appInfo.version}` : ""}`}
        />
        <nav className="titlebar-menu" aria-label={locale === "zh-CN" ? "应用菜单" : "Application menu"}>
          {MENU_IDS.map((menu) => (
            <button
              key={menu}
              className="titlebar-menu-button"
              aria-haspopup="menu"
              onClick={(event) => openMenu(event, menu)}
            >
              {locale === "zh-CN" ? MENU_ZH[menu] : menu}
            </button>
          ))}
        </nav>
      </div>

      <div className="titlebar-center">
        {project ? (
          <span className="project-pill" title={project.path}>
            <span className="dim">◱</span>
            <span className="name">
              {locale === "zh-CN" && project.title_zh ? project.title_zh : project.title}
            </span>
            {dirtyCount > 0 && (
              <span
                className="titlebar-dirty"
                title={
                  locale === "zh-CN"
                    ? `${dirtyCount} 个章节未保存（Ctrl+S 保存）`
                    : `${dirtyCount} unsaved section(s) — Ctrl+S to save`
                }
              >
                ● {dirtyCount}
              </span>
            )}
            <button
              className="titlebar-icon-button"
              onClick={closeProject}
              title={locale === "zh-CN" ? "关闭项目" : "Close project"}
              aria-label={locale === "zh-CN" ? "关闭项目" : "Close project"}
            >
              ×
            </button>
          </span>
        ) : (
          <span className="titlebar-empty">
            PaperCreator · {locale === "zh-CN" ? "论文工作台" : "Research workbench"}
          </span>
        )}

        {projects.length > 1 && (
          <select
            className="titlebar-project-select"
            value={project?.id ?? ""}
            onChange={(event) => {
              const id = event.target.value;
              if (id) void openProject(id);
            }}
            aria-label={locale === "zh-CN" ? "切换项目" : "Switch project"}
            title={locale === "zh-CN" ? "切换项目" : "Switch project"}
          >
            <option value="">{locale === "zh-CN" ? "切换到…" : "Switch to…"}</option>
            {projects.map((entry) => (
              <option key={entry.id} value={entry.id}>{entry.title}</option>
            ))}
          </select>
        )}
      </div>

      <div className="titlebar-right">
        <button
          className={`titlebar-action${assistantOpen ? " active" : ""}`}
          onClick={() => toggleAssistant()}
          title={locale === "zh-CN" ? "打开或关闭右侧 AI 助手" : "Toggle the AI assistant"}
          aria-label={locale === "zh-CN" ? "AI 助手" : "AI assistant"}
        >
          ◇ <span>{locale === "zh-CN" ? "AI 助手" : "Assistant"}</span>
        </button>
        <button
          className="titlebar-action"
          onClick={() => useStore.getState().togglePalette(true)}
          title={locale === "zh-CN" ? "命令面板（Ctrl+Shift+P）" : "Command Palette (Ctrl+Shift+P)"}
          aria-label={locale === "zh-CN" ? "命令面板" : "Command Palette"}
        >
          ⌕ <span>{locale === "zh-CN" ? "命令" : "Command"}</span>
        </button>
        <button
          className="conn"
          onClick={() => setView("output")}
          title={
            connection === "open"
              ? locale === "zh-CN" ? "后端事件流已连接；打开输出" : "Backend event stream connected; open Output"
              : locale === "zh-CN" ? "事件流未连接；打开输出检查" : "Event stream disconnected; inspect Output"
          }
          aria-label={locale === "zh-CN" ? `后端${connectionLabel}` : `Backend ${connectionLabel}`}
        >
          <span className={`dot ${connection}`} />
          <span>{connectionLabel}</span>
        </button>
      </div>
    </header>
  );
}

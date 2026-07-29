/**
 * Application shell.
 *
 * Layout follows VS Code because the target user lives in one: activity bar for
 * top-level areas, a contextual sidebar, the working area, a collapsible bottom
 * panel for output, and a status bar carrying the state that matters continuously
 * (word count, provider health, active jobs).
 */

import { useEffect, useRef, useState } from "react";

import { ActivityBar } from "./components/ActivityBar";
import { AssistantPanel } from "./components/AssistantPanel";
import { CommandPalette } from "./components/CommandPalette";
import { OutputPanel } from "./components/OutputPanel";
import { QuickStartDialog } from "./components/QuickStartDialog";
import { Sidebar } from "./components/Sidebar";
import { StatusBar } from "./components/StatusBar";
import { TitleBar } from "./components/TitleBar";
import { Toasts } from "./components/Toasts";
import { trapFocusIn } from "./components/dialogFocus";
import { useStore } from "./state/store";
import { AgentsView } from "./views/AgentsView";
import { EditorView } from "./views/EditorView";
import { ExportView } from "./views/ExportView";
import { LandscapeView } from "./views/LandscapeView";
import { LibraryView } from "./views/LibraryView";
import { ProjectsView } from "./views/ProjectsView";
import { SearchView } from "./views/SearchView";
import { SettingsView } from "./views/SettingsView";
import { SkillsView } from "./views/SkillsView";
import { VersionsView } from "./views/VersionsView";

export function App() {
  const booted = useStore((s) => s.booted);
  const backendReady = useStore((s) => s.backendReady);
  const backendError = useStore((s) => s.backendError);
  const view = useStore((s) => s.view);
  const panelOpen = useStore((s) => s.panelOpen);
  const sidebarWidth = useStore((s) => s.sidebarWidth);
  const boot = useStore((s) => s.boot);

  useEffect(() => {
    void boot();
  }, [boot]);

  // Native menu accelerators and backend lifecycle events from the main process.
  useEffect(() => {
    const bridge = window.papercreator;
    if (!bridge) return;
    const store = useStore.getState;
    const unsubscribers = [
      bridge.onMenu((command) => {
        const state = store();
        switch (command) {
          case "project.new":
            state.openProjectCreator();
            break;
          case "document.save":
            void state.saveAllSections();
            break;
          case "version.commit":
            state.setView("versions");
            break;
          case "export.open":
            state.setView("export");
            break;
          case "search.open":
            state.setView("search");
            break;
          case "palette.open":
            state.togglePalette(true);
            break;
          case "system.diagnostics":
            state.setView("settings");
            void state.refreshHealth();
            break;
          case "help.quickStart":
            state.openQuickStart();
            break;
        }
      }),
      bridge.lifecycle.onPrepareQuit(async () => {
        await store().saveAllSections();
        const remaining = Object.keys(store().dirtySections).length;
        return remaining === 0
          ? { ok: true }
          : {
              ok: false,
              error:
                store().locale === "zh-CN"
                  ? `仍有 ${remaining} 个章节未能保存`
                  : `${remaining} section(s) could not be saved`,
            };
      }),
      bridge.backend.onLog((line) => store().appendBackendLog(line)),
      bridge.backend.onReady(() => void store().boot()),
      bridge.backend.onFailed(({ message }) =>
        store().notify({
          kind: "error",
          message: store().locale === "zh-CN" ? "后端未能启动" : "The backend did not start",
          detail: message,
          action: { label: store().locale === "zh-CN" ? "查看输出" : "Show output", view: "output" },
        }),
      ),
      bridge.backend.onExit(({ code }) =>
        store().notify({
          kind: "error",
          message:
            store().locale === "zh-CN"
              ? `后端已停止（退出代码 ${code ?? "未知"}）`
              : `The backend stopped (exit code ${code ?? "unknown"})`,
          detail:
            store().locale === "zh-CN"
              ? "请从状态栏重新启动，或检查输出面板。"
              : "Restart it from the status bar, or check the Output panel.",
          action: { label: store().locale === "zh-CN" ? "查看输出" : "Show output", view: "output" },
        }),
      ),
    ];
    return () => unsubscribers.forEach((u) => u());
  }, []);

  // Keyboard shortcuts that must work regardless of focus.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Tab") {
        const modals = document.querySelectorAll<HTMLElement>(".modal-backdrop .modal");
        const activeModal = modals[modals.length - 1];
        if (activeModal) trapFocusIn(activeModal, event);
      }
      const mod = event.ctrlKey || event.metaKey;
      if (mod && event.shiftKey && event.key.toLowerCase() === "p") {
        event.preventDefault();
        useStore.getState().togglePalette();
      } else if (mod && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void useStore.getState().saveAllSections();
      } else if (mod && event.key === "`") {
        event.preventDefault();
        useStore.getState().togglePanel();
      } else if (event.key === "Escape") {
        useStore.getState().togglePalette(false);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  if (!booted) {
    const locale = useStore.getState().locale;
    return (
      <div className="shell transient-shell">
        <TitleBar />
        <div className="empty" style={{ paddingTop: "22vh" }}>
          <div className="big">◱</div>
          <div>{locale === "zh-CN" ? "正在启动 PaperCreator…" : "Starting PaperCreator…"}</div>
          <div className="dim" style={{ marginTop: 6 }}>
            {locale === "zh-CN" ? "正在等待本地后端" : "waiting for the local backend"}
          </div>
        </div>
      </div>
    );
  }

  if (!backendReady) {
    return (
      <div className="shell transient-shell">
        <TitleBar />
        <BackendDown message={backendError} />
      </div>
    );
  }

  return (
    <div className="shell">
      <TitleBar />
      <div className="body">
        <ActivityBar />
        <SidebarWithResizer width={sidebarWidth} />
        <div className="main">
          <div className="main-content">{renderView(view)}</div>
          {panelOpen && <OutputPanel />}
        </div>
        <AssistantPanel />
      </div>
      <StatusBar />
      <Toasts />
      <CommandPalette />
      <QuickStartDialog />
    </div>
  );
}

function renderView(view: string) {
  switch (view) {
    case "projects":
      return <ProjectsView />;
    case "search":
      return <SearchView />;
    case "library":
      return <LibraryView />;
    case "landscape":
      return <LandscapeView />;
    case "editor":
      return <EditorView />;
    case "agents":
      return <AgentsView />;
    case "versions":
      return <VersionsView />;
    case "export":
      return <ExportView />;
    case "skills":
      return <SkillsView />;
    case "settings":
      return <SettingsView />;
    case "output":
      return <OutputPanel standalone />;
    default:
      return <ProjectsView />;
  }
}

/** Sidebar plus its drag handle. Width is kept in the store so it persists. */
function SidebarWithResizer({ width }: { width: number }) {
  const setSidebarWidth = useStore((s) => s.setSidebarWidth);
  const locale = useStore((s) => s.locale);
  const [dragging, setDragging] = useState(false);
  const startRef = useRef({ x: 0, width });

  useEffect(() => {
    if (!dragging) return;
    function onMove(event: MouseEvent) {
      setSidebarWidth(startRef.current.width + (event.clientX - startRef.current.x));
    }
    function onUp() {
      setDragging(false);
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    // Without this the cursor flickers between col-resize and text while dragging.
    document.body.style.cursor = "col-resize";
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
    };
  }, [dragging, setSidebarWidth]);

  return (
    <>
      <div className="sidebar" style={{ width }}>
        <Sidebar />
      </div>
      <div
        className={`resizer${dragging ? " dragging" : ""}`}
        onMouseDown={(event) => {
          startRef.current = { x: event.clientX, width };
          setDragging(true);
        }}
        role="separator"
        aria-label={locale === "zh-CN" ? "调整侧边栏宽度" : "Resize sidebar"}
      />
    </>
  );
}

function BackendDown({ message }: { message: string }) {
  const [restarting, setRestarting] = useState(false);
  const [appInfo, setAppInfo] = useState<{
    isDev: boolean;
    bundledBackendExists: boolean;
  } | null>(null);
  const boot = useStore((s) => s.boot);
  const locale = useStore((s) => s.locale);

  useEffect(() => {
    void window.papercreator?.appInfo().then((info) =>
      setAppInfo({
        isDev: info.isDev,
        bundledBackendExists: info.bundledBackendExists,
      }),
    );
  }, []);

  async function restart() {
    setRestarting(true);
    try {
      await window.papercreator?.backend.restart();
      await boot();
    } finally {
      setRestarting(false);
    }
  }

  return (
    <div className="view" style={{ paddingTop: "10vh", maxWidth: 760 }}>
      <h1>{locale === "zh-CN" ? "后端没有响应" : "The backend is not responding"}</h1>
      <p className="sub">
        {locale === "zh-CN"
          ? "PaperCreator 依赖本地 Python 服务；服务恢复前，界面无法执行项目操作。"
          : "PaperCreator runs a local Python service. The interface cannot do anything until it is reachable."}
      </p>
      <div className="card">
        <h3>{locale === "zh-CN" ? "发生了什么" : "What happened"}</h3>
        <div className="mono" style={{ whiteSpace: "pre-wrap" }}>
          {message || (locale === "zh-CN" ? "没有收到详细信息。" : "No details were reported.")}
        </div>
      </div>
      {appInfo?.isDev ? (
        <div className="card">
          <h3>{locale === "zh-CN" ? "开发环境诊断" : "Development diagnostics"}</h3>
          <ol style={{ margin: "0 0 0 18px", padding: 0 }}>
            <li>
              {locale === "zh-CN" ? "在源码仓库终端中运行诊断：" : "Run diagnostics from the repository terminal:"}
              <div className="mono" style={{ marginTop: 4 }}>
                .\.venv\Scripts\python.exe -m papercreator --check
              </div>
            </li>
            <li style={{ marginTop: 8 }}>
              {locale === "zh-CN" ? "修复诊断结果后重新启动本地服务。" : "Resolve the reported issue, then restart the local service."}
            </li>
          </ol>
        </div>
      ) : (
        <div className="card">
          <h3>{locale === "zh-CN" ? "恢复步骤" : "Recovery steps"}</h3>
          <p className="muted" style={{ margin: 0 }}>
            {appInfo && !appInfo.bundledBackendExists
              ? locale === "zh-CN"
                ? "安装包中的本地服务文件缺失。请重新安装 PaperCreator；工作台数据不会被删除。"
                : "The installed local service is missing. Reinstall PaperCreator; your workbench data will not be removed."
              : locale === "zh-CN"
                ? "先重新启动本地服务。如果仍然失败，请打开日志目录并保留最新日志用于排查。"
                : "Restart the local service first. If it still fails, open the log folder and keep the latest log for diagnosis."}
          </p>
        </div>
      )}
      <div className="row">
        {window.papercreator && (
          <button className="btn primary" onClick={restart} disabled={restarting}>
            {restarting ? (locale === "zh-CN" ? "正在重启…" : "Restarting…") : (locale === "zh-CN" ? "重启后端" : "Restart backend")}
          </button>
        )}
        <button className="btn" onClick={() => void boot()} disabled={restarting}>
          {locale === "zh-CN" ? "重试连接" : "Retry connection"}
        </button>
        {window.papercreator && (
          <button className="btn" onClick={() => void window.papercreator?.shell.openLogs()}>
            {locale === "zh-CN" ? "打开日志目录" : "Open log folder"}
          </button>
        )}
      </div>
    </div>
  );
}

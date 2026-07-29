import { useEffect, useId, useRef } from "react";

import { useStore } from "../state/store";
import { trapDialogFocus } from "./dialogFocus";

interface QuickStartStep {
  id: string;
  title: string;
  detail: string;
  action: string;
  complete: boolean;
  run: () => void;
}

export function QuickStartDialog() {
  const open = useStore((s) => s.quickStartOpen);
  const close = useStore((s) => s.closeQuickStart);
  const dismiss = useStore((s) => s.dismissQuickStart);
  const locale = useStore((s) => s.locale);
  const projects = useStore((s) => s.projects);
  const activeProjectId = useStore((s) => s.activeProjectId);
  const workbench = useStore((s) => s.workbench);
  const stats = useStore((s) => s.stats);
  const timeline = useStore((s) => s.timeline);
  const hasLlm = useStore((s) => s.health?.llm.has_any ?? false);
  const dialogTitleId = useId();
  const dialogDescriptionId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    requestAnimationFrame(() => closeRef.current?.focus());
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      previousFocus?.focus();
    };
  }, [close, open]);

  if (!open) return null;

  const zh = locale === "zh-CN";
  const setView = useStore.getState().setView;
  const navigate = (run: () => void) => () => {
    close();
    run();
  };
  const hasProject = projects.length > 0;
  const sourceCount = (workbench?.categories ?? []).reduce(
    (total, category) => total + category.count,
    0,
  );
  const hasSources =
    sourceCount > 0 ||
    projects.some((project) => project.paper_count > 0) ||
    (stats?.papers_in_project ?? 0) > 0;
  const hasWriting =
    projects.some((project) => project.word_count > 0) || (stats?.words ?? 0) > 0;
  const hasVersion = Boolean(activeProjectId && timeline.length > 0);

  const steps: QuickStartStep[] = [
    {
      id: "workbench",
      title: zh ? "确认研究工作台" : "Know your workbench",
      detail: zh
        ? "资料、项目、设置和日志都保存在当前工作台的 .papercreator 目录中。"
        : "Research material, projects, settings and logs stay in this workbench's .papercreator directory.",
      action: zh ? "查看工作台" : "View workbench",
      complete: Boolean(workbench),
      run: () => setView("projects"),
    },
    {
      id: "project",
      title: zh ? "创建或打开论文项目" : "Create or open a paper project",
      detail: zh
        ? "项目把研究想法、文献集合、手稿、图谱和版本历史组织在一起。"
        : "A project connects the research idea, paper collection, manuscript, landscape and versions.",
      action: hasProject ? (zh ? "查看项目" : "View projects") : (zh ? "创建项目" : "Create project"),
      complete: hasProject,
      run: () => hasProject ? setView("projects") : useStore.getState().openProjectCreator(),
    },
    {
      id: "sources",
      title: zh ? "加入研究资料" : "Add research sources",
      detail: zh
        ? "导入已有资料，或在项目中检索论文并加入文献库。"
        : "Import existing material, or search for papers and add them to a project's library.",
      action: activeProjectId ? (zh ? "检索论文" : "Search papers") : (zh ? "导入资料" : "Import sources"),
      complete: hasSources,
      run: () => setView(activeProjectId ? "search" : "projects"),
    },
    {
      id: "manuscript",
      title: zh ? "撰写并保存手稿" : "Write and save the manuscript",
      detail: zh
        ? "在章节编辑器中写作；保存后内容会同步到数据库和可迁移文件。"
        : "Write in the section editor; saving syncs the manuscript to the database and portable files.",
      action: activeProjectId ? (zh ? "打开手稿" : "Open manuscript") : (zh ? "选择项目" : "Choose project"),
      complete: hasWriting,
      run: () => setView(activeProjectId ? "editor" : "projects"),
    },
    {
      id: "version",
      title: zh ? "保存一个可恢复版本" : "Save a recoverable version",
      detail: zh
        ? "本地快照和 Git 版本让重要修改可以比较和恢复，不会自动联网推送。"
        : "Local snapshots and Git versions make important edits comparable and recoverable without auto-pushing online.",
      action: activeProjectId ? (zh ? "打开版本" : "Open versions") : (zh ? "选择项目" : "Choose project"),
      complete: hasVersion,
      run: () => setView(activeProjectId ? "versions" : "projects"),
    },
  ];
  const completed = steps.filter((step) => step.complete).length;
  const allComplete = completed === steps.length;

  return (
    <div className="modal-backdrop quick-start-backdrop" onClick={close}>
      <div
        className="modal quick-start-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={dialogTitleId}
        aria-describedby={dialogDescriptionId}
        onClick={(event) => event.stopPropagation()}
        onKeyDown={trapDialogFocus}
      >
        <header>
          <div>
            <div id={dialogTitleId}>{zh ? "快速开始" : "Quick start"}</div>
            <div className="quick-start-progress-copy">
              {zh ? `已完成 ${completed} / ${steps.length}` : `${completed} of ${steps.length} complete`}
            </div>
          </div>
          <button
            ref={closeRef}
            className="btn icon sm"
            onClick={close}
            aria-label={zh ? "关闭快速开始" : "Close quick start"}
          >
            ✕
          </button>
        </header>
        <div className="quick-start-progress" aria-hidden="true">
          <span style={{ width: `${(completed / steps.length) * 100}%` }} />
        </div>
        <div className="modal-body">
          <p id={dialogDescriptionId} className="quick-start-intro">
            {zh
              ? "按研究工作流逐步推进。每项进度都来自当前工作台的真实状态。"
              : "Move through the research workflow. Each item reflects the workbench's actual state."}
          </p>
          <ol className="quick-start-steps">
            {steps.map((step, index) => (
              <li key={step.id} className={step.complete ? "complete" : ""}>
                <span className="quick-start-state" aria-hidden="true">
                  {step.complete ? "✓" : index + 1}
                </span>
                <div className="quick-start-copy">
                  <strong>{step.title}</strong>
                  <span>{step.detail}</span>
                </div>
                <button className="btn sm" onClick={navigate(step.run)}>
                  {step.complete ? (zh ? "查看" : "Open") : step.action}
                </button>
              </li>
            ))}
          </ol>
          <div className="quick-start-next">
            <span>
              {zh ? "可选增强" : "Optional next steps"}
              <small>
                {hasLlm
                  ? (zh ? "AI 服务已配置" : "AI service configured")
                  : (zh ? "配置 AI 服务后可使用智能体辅助写作" : "Configure an AI service to use writing agents")}
              </small>
            </span>
            <button className="btn sm" onClick={navigate(() => setView("settings"))}>
              {zh ? "打开设置" : "Open settings"}
            </button>
            {activeProjectId && (
              <button className="btn sm" onClick={navigate(() => setView("export"))}>
                {zh ? "导出论文" : "Export paper"}
              </button>
            )}
          </div>
        </div>
        <footer>
          <button className="btn" onClick={close}>
            {zh ? "稍后再说" : "Later"}
          </button>
          <button
            className="btn primary"
            onClick={() => void dismiss()}
            title={
              allComplete
                ? undefined
                : zh
                  ? "关闭本工作台的自动提示；仍可从帮助菜单重新打开"
                  : "Stop showing this automatically; it remains available from Help"
            }
          >
            {allComplete
              ? (zh ? "完成" : "Done")
              : (zh ? "不再自动显示" : "Don't show automatically")}
          </button>
        </footer>
      </div>
    </div>
  );
}

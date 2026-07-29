/**
 * Command palette (Ctrl+Shift+P).
 *
 * Every non-trivial action is reachable from here, which is what makes a dense
 * tool usable without hunting through views. Commands that cannot run right now
 * are still listed, with the reason - discoverability matters more than a short
 * list, and a hidden command teaches the user nothing.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import * as endpoints from "../api/endpoints";
import { useStore, type ViewId } from "../state/store";
import { trapDialogFocus } from "./dialogFocus";

interface Command {
  id: string;
  title: string;
  titleZh: string;
  group: string;
  run: () => void | Promise<void>;
  /** Reason it cannot run, or `""` when it can. */
  blocked?: string;
}

export function CommandPalette() {
  const open = useStore((s) => s.paletteOpen);
  const togglePalette = useStore((s) => s.togglePalette);
  const locale = useStore((s) => s.locale);
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const commands = useCommands();

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return commands;
    // Subsequence match, like the editor's: "bl" finds "Build Landscape".
    return commands.filter((command) => {
      const haystack = `${command.title} ${command.titleZh} ${command.group}`.toLowerCase();
      let position = 0;
      for (const character of needle) {
        if (character === " ") continue;
        position = haystack.indexOf(character, position);
        if (position === -1) return false;
        position += 1;
      }
      return true;
    });
  }, [commands, query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setIndex(0);
      // Focus after the element exists, or the keystroke that opened the palette
      // lands on the previous focus target.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => {
    setIndex(0);
  }, [query]);

  if (!open) return null;

  function execute(command: Command) {
    if (command.blocked) {
      useStore.getState().notify({ kind: "info", message: command.blocked });
      return;
    }
    togglePalette(false);
    void command.run();
  }

  return (
    <div
      className="palette-backdrop"
      onClick={() => togglePalette(false)}
      role="dialog"
      aria-modal="true"
      aria-label={locale === "zh-CN" ? "命令面板" : "Command palette"}
      onKeyDown={trapDialogFocus}
    >
      <div className="palette" onClick={(event) => event.stopPropagation()}>
        <input
          ref={inputRef}
          value={query}
          placeholder={
            locale === "zh-CN" ? "输入命令…" : "Type a command…"
          }
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setIndex((current) => Math.min(filtered.length - 1, current + 1));
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setIndex((current) => Math.max(0, current - 1));
            } else if (event.key === "Enter") {
              event.preventDefault();
              const command = filtered[index];
              if (command) execute(command);
            }
          }}
        />
        <div className="palette-list">
          {filtered.map((command, position) => (
            <div
              key={command.id}
              className={`palette-item${position === index ? " active" : ""}`}
              onMouseEnter={() => setIndex(position)}
              onClick={() => execute(command)}
              style={command.blocked ? { opacity: 0.5 } : undefined}
              title={command.blocked || undefined}
            >
              <span className="dim" style={{ minWidth: 74, fontSize: 11 }}>
                {command.group}
              </span>
              <span>{locale === "zh-CN" ? command.titleZh : command.title}</span>
              {command.blocked && <span className="where">{command.blocked}</span>}
            </div>
          ))}
          {!filtered.length && (
            <div className="palette-item dim">
              {locale === "zh-CN" ? "无匹配命令" : "No matching command"}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function useCommands(): Command[] {
  const locale = useStore((s) => s.locale);
  const hasProject = Boolean(useStore((s) => s.activeProjectId));
  const hasAnalysis = Boolean(useStore((s) => s.analysis));
  const hasLlm = useStore((s) => s.health?.llm.has_any ?? false);
  const paperCount = useStore((s) => s.stats?.papers_in_project ?? 0);
  const dirtyCount = useStore((s) => Object.keys(s.dirtySections).length);

  return useMemo(() => {
    const store = useStore.getState;
    const needProject = hasProject ? "" : locale === "zh-CN" ? "需要先打开一个项目" : "needs an open project";
    const needPapers =
      paperCount > 0 ? "" : locale === "zh-CN" ? "需要论文，请先运行检索" : "needs papers — run a search first";
    const needLlm = hasLlm ? "" : locale === "zh-CN" ? "需要先在设置中配置 LLM 服务" : "needs an LLM provider in Settings";

    const go = (view: ViewId) => () => store().setView(view);

    const commands: Command[] = [
      {
        id: "help.quickStart",
        group: "Help",
        title: "Open quick start",
        titleZh: "打开快速开始",
        run: () => store().openQuickStart(),
      },
      { id: "go.projects", group: "Go to", title: "Projects", titleZh: "项目", run: go("projects") },
      { id: "go.search", group: "Go to", title: "Search", titleZh: "检索", run: go("search") },
      { id: "go.library", group: "Go to", title: "Library", titleZh: "文献库", run: go("library") },
      { id: "go.landscape", group: "Go to", title: "Landscape", titleZh: "研究图谱", run: go("landscape"), blocked: needProject },
      { id: "go.editor", group: "Go to", title: "Manuscript", titleZh: "手稿", run: go("editor"), blocked: needProject },
      { id: "go.agents", group: "Go to", title: "Agents", titleZh: "智能体", run: go("agents"), blocked: needProject },
      { id: "go.versions", group: "Go to", title: "Versions", titleZh: "版本历史", run: go("versions"), blocked: needProject },
      { id: "go.export", group: "Go to", title: "Export", titleZh: "导出", run: go("export"), blocked: needProject },
      { id: "go.skills", group: "Go to", title: "Skills", titleZh: "技能", run: go("skills") },
      { id: "go.settings", group: "Go to", title: "Settings", titleZh: "设置", run: go("settings") },

      {
        id: "doc.save",
        group: "Manuscript",
        title: `Save all sections${dirtyCount ? ` (${dirtyCount})` : ""}`,
        titleZh: "保存全部章节",
        run: () => store().saveAllSections(),
        blocked: dirtyCount ? "" : locale === "zh-CN" ? "没有需要保存的内容" : "nothing to save",
      },

      {
        id: "analysis.build",
        group: "Analysis",
        title: "Build landscape",
        titleZh: "生成研究图谱",
        run: () => store().buildAnalysis(),
        blocked: needProject || needPapers,
      },
      {
        id: "analysis.rebuild.umap",
        group: "Analysis",
        title: "Rebuild landscape with UMAP + HDBSCAN",
        titleZh: "用 UMAP + HDBSCAN 重建图谱",
        run: () => store().buildAnalysis({ reducer: "umap", clusterer: "hdbscan" }),
        blocked: needProject || needPapers,
      },
      {
        id: "analysis.graph",
        group: "Analysis",
        title: "Open citation graph",
        titleZh: "查看引用网络",
        run: go("landscape"),
        blocked: hasAnalysis ? "" : "needs a landscape",
      },

      {
        id: "agent.full",
        group: "Agents",
        title: "Write the whole paper (full auto)",
        titleZh: "一次生成全文",
        run: () => store().startAgentRun({ pipeline: "full_auto" }),
        blocked: needProject || needLlm,
      },
      {
        id: "agent.section",
        group: "Agents",
        title: "Draft the current section",
        titleZh: "撰写当前章节",
        run: () => {
          const key = store().activeSectionKey;
          return store().startAgentRun({
            pipeline: "section",
            section_keys: key ? [key] : [],
          });
        },
        blocked: needProject || needLlm,
      },
      {
        id: "agent.stitch",
        group: "Agents",
        title: "Stitch the sections together",
        titleZh: "拼接成文",
        run: () => store().startAgentRun({ pipeline: "stitch" }),
        blocked: needProject || needLlm,
      },

      {
        id: "version.save",
        group: "Versions",
        title: "Save a local version (snapshot + commit)",
        titleZh: "保存本地版本（快照 + 提交）",
        run: async () => {
          const { activeProjectId, notify, loadTimeline, reportError } = store();
          try {
            await endpoints.versions.save(activeProjectId, { label: "manual save" });
            await loadTimeline();
            notify({ kind: "success", message: locale === "zh-CN" ? "本地版本已保存" : "Version saved" });
          } catch (error) {
            reportError(error, locale === "zh-CN" ? "保存本地版本" : "saving the version");
          }
        },
        blocked: needProject,
      },

      {
        id: "export.latex",
        group: "Export",
        title: "Export LaTeX project",
        titleZh: "导出 LaTeX 项目",
        run: go("export"),
        blocked: needProject,
      },
      {
        id: "export.docx",
        group: "Export",
        title: "Export Word document",
        titleZh: "导出 Word 文档",
        run: go("export"),
        blocked: needProject,
      },

      {
        id: "system.output",
        group: "View",
        title: "Toggle output panel",
        titleZh: "切换输出面板",
        run: () => store().togglePanel(),
      },
      {
        id: "system.health",
        group: "View",
        title: "Refresh backend diagnostics",
        titleZh: "刷新后端诊断",
        run: () => store().refreshHealth(),
      },
      {
        id: "system.locale",
        group: "View",
        title: "Switch interface language",
        titleZh: "切换界面语言",
        run: () =>
          store().setLocale(locale === "zh-CN" ? "en-US" : "zh-CN"),
      },
    ];
    return commands;
  }, [hasProject, hasAnalysis, hasLlm, paperCount, dirtyCount, locale]);
}

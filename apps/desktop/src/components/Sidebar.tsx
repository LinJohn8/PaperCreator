/**
 * Contextual sidebar. Content follows the active area, like VS Code's.
 *
 * The manuscript outline is the most-used one: it doubles as navigation and as a
 * progress readout, showing per-section word count against target and draft
 * status, so the user can see where the paper is thin without opening each
 * section.
 */

import { useStore } from "../state/store";
import type { SectionStatus } from "../api/types";

export function Sidebar() {
  const view = useStore((s) => s.view);
  switch (view) {
    case "editor":
    case "agents":
    case "versions":
    case "export":
      return <OutlinePane />;
    case "landscape":
      return <ClusterPane />;
    case "library":
    case "search":
      return <LibraryPane />;
    case "skills":
      return <SkillPane />;
    default:
      return <ProjectPane />;
  }
}

const STATUS_MARK: Record<SectionStatus, string> = {
  empty: "○",
  drafting: "◐",
  drafted: "◑",
  reviewed: "◕",
  final: "●",
};

function OutlinePane() {
  const document = useStore((s) => s.document);
  const stats = useStore((s) => s.stats);
  const activeSectionKey = useStore((s) => s.activeSectionKey);
  const setActiveSection = useStore((s) => s.setActiveSection);
  const dirtySections = useStore((s) => s.dirtySections);
  const setView = useStore((s) => s.setView);
  const locale = useStore((s) => s.locale);
  const project = useStore((s) => s.project);

  const detail = new Map((stats?.sections_detail ?? []).map((d) => [d.key, d]));

  return (
    <>
      <div className="sidebar-header">
        <span>{locale === "zh-CN" ? "手稿结构" : "Outline"}</span>
        <button
          className="btn icon sm"
          title={locale === "zh-CN" ? "应用模板" : "Apply a template"}
          onClick={() => setView("editor")}
        >
          ＋
        </button>
      </div>
      <div className="sidebar-body">
        {!document?.sections.length ? (
          <div className="empty" style={{ padding: 24 }}>
            <div className="dim">
              {locale === "zh-CN"
                ? "还没有章节。在手稿视图中应用一个模板。"
                : "No sections yet. Apply a template from the Manuscript view."}
            </div>
          </div>
        ) : (
          document.sections.map((section) => {
            const info = detail.get(section.key);
            const dirty = dirtySections[section.key] !== undefined;
            const behind =
              info && info.target > 0 && info.words < info.target * 0.6;
            return (
              <div
                key={section.key}
                className={`tree-item${activeSectionKey === section.key ? " active" : ""}`}
                style={{ paddingLeft: 12 + section.level * 8 }}
                onClick={() => setActiveSection(section.key)}
                title={section.guidance || section.title}
              >
                <span
                  className="dim"
                  title={
                    locale === "zh-CN"
                      ? `状态：${({ empty: "空白", drafting: "撰写中", drafted: "已起草", reviewed: "已审阅", final: "定稿" } as Record<string, string>)[section.status]}`
                      : `status: ${section.status}`
                  }
                  style={{ flex: "none" }}
                >
                  {STATUS_MARK[section.status]}
                </span>
                <span className="label">
                  {locale === "zh-CN" && section.title_zh ? section.title_zh : section.title}
                </span>
                {dirty && <span className="dirty" title={locale === "zh-CN" ? "未保存" : "unsaved"} />}
                <span
                  className="meta outline-counts"
                  style={behind ? { color: "var(--warn)" } : undefined}
                  title={
                    info?.target
                      ? locale === "zh-CN" ? `${info.words}/${info.target} 目标字数` : `${info.words} of ${info.target} target words`
                      : locale === "zh-CN" ? `${info?.words ?? 0} 字/词` : `${info?.words ?? 0} words`
                  }
                >
                  <span title={project?.language === "zh" ? "中文（主语言）" : locale === "zh-CN" ? "英文（主语言）" : "English (primary)"}>
                    {info?.words ?? 0}{info?.target ? `/${info.target}` : ""}
                  </span>
                  {project?.bilingual && (
                    <span title={project.language === "zh" ? (locale === "zh-CN" ? "英文（对照）" : "English (paired)") : "中文（对照）"}>
                      {info?.words_zh ?? 0}{info?.target_zh ? `/${info.target_zh}` : ""}
                    </span>
                  )}
                </span>
              </div>
            );
          })
        )}
      </div>
    </>
  );
}

function ClusterPane() {
  const analysis = useStore((s) => s.analysis);
  const highlighted = useStore((s) => s.highlightedClusters);
  const toggleCluster = useStore((s) => s.toggleClusterHighlight);
  const selectGap = useStore((s) => s.selectGap);
  const selectedGapId = useStore((s) => s.selectedGapId);
  const locale = useStore((s) => s.locale);

  if (!analysis) {
    return (
      <>
        <div className="sidebar-header">
          {locale === "zh-CN" ? "研究图谱" : "Landscape"}
        </div>
        <div className="empty" style={{ padding: 24 }}>
          <div className="dim">
            {locale === "zh-CN"
              ? "尚未生成图谱。"
              : "No landscape built yet."}
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="sidebar-header">
        <span>{locale === "zh-CN" ? "主题簇" : "Clusters"}</span>
        <span className="dim">{analysis.clusters.length}</span>
      </div>
      <div className="sidebar-body">
        {analysis.clusters.map((cluster) => {
          const dim = highlighted.length > 0 && !highlighted.includes(cluster.id);
          return (
            <div
              key={cluster.id}
              className={`tree-item${dim ? " dim" : ""}`}
              onClick={() => toggleCluster(cluster.id)}
              title={`${cluster.size} papers, coherence ${cluster.coherence.toFixed(2)}\n${cluster.keywords.slice(0, 8).join(", ")}`}
            >
              <span
                className="legend-swatch"
                style={{ background: `var(--c${cluster.id % 10})` }}
              />
              <span className="label">
                {locale === "zh-CN" && cluster.label_zh ? cluster.label_zh : cluster.label}
              </span>
              <span className="meta">{cluster.size}</span>
            </div>
          );
        })}

        <div className="tree-section">
          {locale === "zh-CN" ? "缺口候选" : "Gap candidates"} ({analysis.gaps.length})
        </div>
        {analysis.gaps.map((gap) => (
          <div
            key={gap.id}
            className={`tree-item${selectedGapId === gap.id ? " active" : ""}`}
            onClick={() => selectGap(gap.id)}
            title={gap.description}
          >
            <span className="dim" style={{ flex: "none" }}>
              {gap.score >= 0.6 ? "◆" : gap.score >= 0.4 ? "◈" : "◇"}
            </span>
            <span className="label">{gap.kind.replace(/_/g, " ")}</span>
            <span className="meta">{gap.score.toFixed(2)}</span>
          </div>
        ))}
        {!analysis.gaps.length && (
          <div className="tree-item dim">
            <span className="label">
              {locale === "zh-CN" ? "未发现缺口" : "none detected"}
            </span>
          </div>
        )}
      </div>
    </>
  );
}

function LibraryPane() {
  const stats = useStore((s) => s.stats);
  const libraryTotal = useStore((s) => s.libraryTotal);
  const selected = useStore((s) => s.selectedPaperIds);
  const providers = useStore((s) => s.providers);
  const locale = useStore((s) => s.locale);
  const clearSelection = useStore((s) => s.clearSelection);

  return (
    <>
      <div className="sidebar-header">{locale === "zh-CN" ? "文献" : "Papers"}</div>
      <div className="sidebar-body" style={{ padding: "8px 16px" }}>
        <dl className="kv">
          <dt>{locale === "zh-CN" ? "当前列表" : "In list"}</dt>
          <dd>{libraryTotal}</dd>
          {stats && (
            <>
              <dt>{locale === "zh-CN" ? "项目内" : "In project"}</dt>
              <dd>{stats.papers_in_project}</dd>
              <dt>{locale === "zh-CN" ? "已引用" : "Cited"}</dt>
              <dd>{stats.papers_cited}</dd>
            </>
          )}
          <dt>{locale === "zh-CN" ? "已选中" : "Selected"}</dt>
          <dd>
            {selected.length}
            {selected.length > 0 && (
              <button className="btn sm" style={{ marginLeft: 8 }} onClick={clearSelection}>
                {locale === "zh-CN" ? "清除" : "clear"}
              </button>
            )}
          </dd>
        </dl>

        <div className="tree-section" style={{ padding: "12px 0 4px" }}>
          {locale === "zh-CN" ? "检索源" : "Sources"}
        </div>
        {providers.map((provider) => (
          <div
            key={provider.id}
            className="row"
            style={{ padding: "2px 0", fontSize: "var(--fs-sm)" }}
            title={provider.unavailable_reason || provider.description}
          >
            <span className={`dot ${provider.available ? "open" : "closed"}`} />
            <span className="grow truncate">
              {locale === "zh-CN" && provider.name_zh ? provider.name_zh : provider.name}
            </span>
            <span className="dim">
              {locale === "zh-CN" && provider.tier === "free" ? "免费" : provider.tier}
            </span>
          </div>
        ))}
      </div>
    </>
  );
}

function SkillPane() {
  const skills = useStore((s) => s.skills);
  const enabled = useStore((s) => s.enabledSkillIds);
  const toggleSkill = useStore((s) => s.toggleSkill);
  const locale = useStore((s) => s.locale);

  const byScope = {
    builtin: skills.filter((s) => s.scope === "builtin"),
    user: skills.filter((s) => s.scope === "user"),
    project: skills.filter((s) => s.scope === "project"),
  };

  return (
    <>
      <div className="sidebar-header">
        <span>{locale === "zh-CN" ? "技能" : "Skills"}</span>
        <span className="dim">
          {enabled.length}/{skills.length}
        </span>
      </div>
      <div className="sidebar-body">
        {(["project", "user", "builtin"] as const).map((scope) =>
          byScope[scope].length ? (
            <div key={scope}>
              <div className="tree-section">{scope}</div>
              {byScope[scope].map((skill) => (
                <div
                  key={skill.id}
                  className="tree-item"
                  onClick={() => toggleSkill(skill.id)}
                  title={skill.description}
                >
                  <span style={{ flex: "none" }}>
                    {enabled.includes(skill.id) ? "☑" : "☐"}
                  </span>
                  <span className="label">{skill.name}</span>
                  <span className="meta">{skill.applies_to.join(",").slice(0, 12)}</span>
                </div>
              ))}
            </div>
          ) : null,
        )}
      </div>
    </>
  );
}

function ProjectPane() {
  const projects = useStore((s) => s.projects);
  const activeProjectId = useStore((s) => s.activeProjectId);
  const openProject = useStore((s) => s.openProject);
  const locale = useStore((s) => s.locale);

  return (
    <>
      <div className="sidebar-header">
        <span>{locale === "zh-CN" ? "工作台 · 论文项目" : "Workbench · Paper projects"}</span>
        <span className="dim">{projects.length}</span>
      </div>
      <div className="sidebar-body">
        {projects.map((project) => (
          <div
            key={project.id}
            className={`tree-item${activeProjectId === project.id ? " active" : ""}`}
            onClick={() => void openProject(project.id)}
            title={`${project.path}\n${project.paper_count} papers, ${project.word_count} words`}
          >
            <span className="dim" style={{ flex: "none" }}>
              ◱
            </span>
            <span className="label">{project.title}</span>
            <span className="meta">{project.paper_count}</span>
          </div>
        ))}
        {!projects.length && (
          <div className="empty" style={{ padding: 24 }}>
            <div className="dim">
              {locale === "zh-CN" ? "还没有项目" : "No projects yet"}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

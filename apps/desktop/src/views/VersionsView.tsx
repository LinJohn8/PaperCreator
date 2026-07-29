/**
 * Versions view: unified git + snapshot history, diff, restore.
 *
 * Both version systems are shown in one timeline because "the history of this
 * paper" is one question. They are not mixed in a diff, though: git versions the
 * files, snapshots version the database including per-section metadata, and a
 * combined diff would be misleading. The UI says which is which.
 */

import { useCallback, useEffect, useState } from "react";

import * as endpoints from "../api/endpoints";
import { useStore } from "../state/store";
import type {
  GitRemote,
  GitRemoteSync,
  GitStatus,
  Project,
  TimelineEntry,
} from "../api/types";

interface GitBranch {
  name: string;
  current: boolean;
  head: string;
}

/**
 * Guard wrapper, so the body receives a non-null project by construction rather
 * than asserting it inside async callbacks.
 */
export function VersionsView() {
  const project = useStore((s) => s.project);
  if (!project) return null;
  return <VersionsBody project={project} />;
}

function VersionsBody({ project }: { project: Project }) {
  const git = useStore((s) => s.git);
  const timeline = useStore((s) => s.timeline);
  const loadTimeline = useStore((s) => s.loadTimeline);
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);

  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [compare, setCompare] = useState<{ left: string; right: string } | null>(null);
  const [diff, setDiff] = useState<Record<string, any> | null>(null);
  const [branches, setBranches] = useState<GitBranch[]>([]);
  const [currentBranch, setCurrentBranch] = useState("");
  const [targetBranch, setTargetBranch] = useState("");
  const [branchName, setBranchName] = useState("");
  const [branchBusy, setBranchBusy] = useState(false);

  const loadBranches = useCallback(async () => {
    if (!git?.is_repo) {
      setBranches([]);
      setCurrentBranch("");
      setTargetBranch("");
      return;
    }
    try {
      const result = await endpoints.versions.branches(project.id);
      const items = result.branches as GitBranch[];
      setBranches(items);
      setCurrentBranch(result.current);
      setTargetBranch((previous) =>
        items.some((branch) => branch.name === previous) ? previous : result.current,
      );
    } catch (error) {
      reportError(error, "loading Git branches");
    }
  }, [git?.is_repo, project.id, reportError]);

  useEffect(() => {
    void loadTimeline();
  }, [loadTimeline]);

  useEffect(() => {
    void loadBranches();
  }, [loadBranches]);

  async function refreshVersions() {
    await loadTimeline();
    await loadBranches();
  }

  async function initialiseGit() {
    setBusy(true);
    try {
      await endpoints.versions.gitInit(project.id);
      await loadTimeline();
      notify({
        kind: "success",
        message: locale === "zh-CN" ? "Git 仓库已初始化并启用" : "Git repository initialised and enabled",
      });
    } catch (error) {
      reportError(error, "initialising Git");
    } finally {
      setBusy(false);
    }
  }

  async function createBranch() {
    const name = branchName.trim();
    if (!name) {
      notify({
        kind: "warning",
        message: locale === "zh-CN" ? "请填写分支名称" : "Enter a branch name",
      });
      return;
    }
    setBranchBusy(true);
    try {
      const result = await endpoints.versions.createBranch(project.id, name);
      await refreshVersions();
      setBranchName("");
      notify({
        kind: "success",
        message: locale === "zh-CN" ? "已创建并切换分支" : "Branch created and checked out",
        detail: String(result.branch ?? name),
      });
    } catch (error) {
      reportError(error, "creating the Git branch");
    } finally {
      setBranchBusy(false);
    }
  }

  async function checkoutBranch() {
    if (!targetBranch || targetBranch === currentBranch) return;
    const confirmed = window.confirm(
      locale === "zh-CN"
        ? `切换到“${targetBranch}”会用该分支的手稿文件重新载入编辑器。系统会先创建安全快照；存在未提交修改时会拒绝切换。继续吗？`
        : `Switch to "${targetBranch}" and reload the editor from that branch's manuscript files? A safety snapshot is created first, and switching is refused when changes are uncommitted.`,
    );
    if (!confirmed) return;
    setBranchBusy(true);
    try {
      const result = await endpoints.versions.checkout(project.id, targetBranch);
      await useStore.getState().reloadDocument();
      await refreshVersions();
      notify({
        kind: "success",
        message: locale === "zh-CN" ? "分支已切换" : "Branch switched",
        detail:
          locale === "zh-CN"
            ? `${String(result.branch ?? targetBranch)}；已创建安全快照并从磁盘重载手稿`
            : `${String(result.branch ?? targetBranch)}; safety snapshot created and manuscript reloaded from disk`,
      });
    } catch (error) {
      reportError(error, "switching Git branches");
    } finally {
      setBranchBusy(false);
    }
  }

  async function saveVersion() {
    setBusy(true);
    try {
      const result = await endpoints.versions.save(project.id, {
        label: message || "manual save",
        commit_message: message,
        snapshot: true,
        git_commit: true,
      });
      const committed = (result.git as any)?.committed;
      notify({
        kind: "success",
        message: committed
          ? locale === "zh-CN" ? "已保存版本并提交" : "Version saved and committed"
          : locale === "zh-CN" ? "已保存快照" : "Snapshot saved",
        detail: committed
          ? undefined
          : String((result.git as any)?.reason ?? (result.git as any)?.commit?.reason ?? ""),
      });
      setMessage("");
      await loadTimeline();
    } catch (error) {
      reportError(error, "saving the version");
    } finally {
      setBusy(false);
    }
  }

  async function runCompare(left: string, right: string) {
    setCompare({ left, right });
    try {
      setDiff(await endpoints.versions.compare(project.id, left, right));
    } catch (error) {
      reportError(error, "comparing versions");
      setDiff(null);
    }
  }

  return (
    <div className="view">
      <h1>{locale === "zh-CN" ? "版本历史" : "Version history"}</h1>
      <p className="sub">
        {locale === "zh-CN"
          ? "默认使用项目文件夹内的本地 Git：提交、分支、对比和恢复都不联网。只有你显式添加 GitHub、GitLab 或其他 remote 后，才会出现可用的 Fetch、Pull 和 Push。"
          : "Local Git inside the project folder is the default: commits, branches, comparisons and restores stay offline. Fetch, Pull and Push only become available after you explicitly add a GitHub, GitLab or other remote."}
      </p>

      <div className="card">
        <h3>{locale === "zh-CN" ? "本地版本控制（默认）" : "Local version control (default)"}</h3>
        <p className="hint">
          {locale === "zh-CN"
            ? "版本写入当前论文项目自己的 .git，不会推送、不会修改源码仓库，也不会改动你的全局 Git 身份。"
            : "Versions are written to this paper project's own .git. Nothing is pushed, the source repository is untouched, and your global Git identity is never changed."}
        </p>
        <div className="row wrap">
          <span className={`chip ${git?.is_repo ? "ok" : "warn"}`}>
            {git?.is_repo
              ? `git · ${git.branch || "?"}`
              : locale === "zh-CN" ? "未初始化 git" : "no git repository"}
          </span>
          {git?.is_repo && (
            <span className={`chip ${git.clean ? "ok" : "warn"}`}>
              {git.clean
                ? locale === "zh-CN" ? "工作区干净" : "clean"
                : `${(git.unstaged?.length ?? 0) + (git.untracked?.length ?? 0)} ${
                    locale === "zh-CN" ? "处未提交" : "uncommitted"
                  }`}
            </span>
          )}
          {git?.git_available === false && (
            <span className="chip err">
              {locale === "zh-CN" ? "系统未安装 git" : "git is not installed"}
            </span>
          )}
          <div className="grow" />
          {!git?.is_repo && git?.git_available && (
            <button
              className="btn sm"
              onClick={() => void initialiseGit()}
              disabled={busy}
            >
              {locale === "zh-CN" ? "启用本地 Git" : "Enable local Git"}
            </button>
          )}
        </div>

        <div className="row" style={{ marginTop: 12 }}>
          <input
            className="grow"
            value={message}
            placeholder={
              locale === "zh-CN"
                ? "版本说明，例如「完成引言初稿」"
                : "What changed, e.g. 'first draft of the introduction'"
            }
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void saveVersion();
            }}
          />
          <button className="btn primary" onClick={() => void saveVersion()} disabled={busy}>
            {git?.is_repo
              ? locale === "zh-CN" ? "本地提交版本" : "Commit local version"
              : locale === "zh-CN" ? "保存快照" : "Save snapshot"}
          </button>
        </div>
        <span className="hint">
          {locale === "zh-CN"
            ? git?.is_repo
              ? "会先把数据库章节写入磁盘，再创建数据库快照和本地 Git 提交；不会自动推送。"
              : "当前只创建数据库快照；启用本地 Git 后才会同时提交磁盘手稿。"
            : git?.is_repo
              ? "Flushes sections to disk, then creates a database snapshot and local Git commit; it never auto-pushes."
              : "Creates a database snapshot only; enable local Git to commit the manuscript files too."}
        </span>
      </div>

      {git?.is_repo && (
        <RemoteGitCard project={project} git={git} onChanged={refreshVersions} />
      )}

      {git?.is_repo && (
        <div className="card" aria-label={locale === "zh-CN" ? "Git 分支" : "Git branches"}>
          <h3>{locale === "zh-CN" ? "探索分支" : "Exploration branches"}</h3>
          <p className="hint">
            {locale === "zh-CN"
              ? "为实验性改写创建独立分支。切换分支会先保存数据库安全快照，再以目标分支的磁盘手稿重建编辑器内容。"
              : "Create a separate branch for an experimental rewrite. Switching first saves a database safety snapshot, then rebuilds the editor from the target branch's manuscript files."}
          </p>
          <div className="row wrap" style={{ marginTop: 10 }}>
            <span className="chip ok">
              {locale === "zh-CN" ? "当前" : "current"}: {currentBranch || git.branch || "?"}
            </span>
            {branches.map((branch) => (
              <span key={branch.name} className={`chip ${branch.current ? "ok" : ""}`}>
                {branch.name} · {branch.head || "?"}
              </span>
            ))}
          </div>
          <div className="row wrap" style={{ marginTop: 10 }}>
            <input
              className="grow"
              aria-label={locale === "zh-CN" ? "新分支名称" : "New branch name"}
              placeholder={locale === "zh-CN" ? "例如：rewrite-method" : "e.g. rewrite-method"}
              value={branchName}
              onChange={(event) => setBranchName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void createBranch();
              }}
            />
            <button className="btn" onClick={() => void createBranch()} disabled={branchBusy}>
              {locale === "zh-CN" ? "创建并切换" : "Create and switch"}
            </button>
          </div>
          {branches.length > 1 && (
            <div className="row wrap" style={{ marginTop: 10 }}>
              <select
                className="grow"
                aria-label={locale === "zh-CN" ? "目标分支" : "Target branch"}
                value={targetBranch}
                onChange={(event) => setTargetBranch(event.target.value)}
              >
                {branches.map((branch) => (
                  <option key={branch.name} value={branch.name}>
                    {branch.name}
                    {branch.current ? (locale === "zh-CN" ? "（当前）" : " (current)") : ""}
                  </option>
                ))}
              </select>
              <button
                className="btn"
                onClick={() => void checkoutBranch()}
                disabled={branchBusy || !targetBranch || targetBranch === currentBranch}
              >
                {locale === "zh-CN" ? "切换分支" : "Switch branch"}
              </button>
            </div>
          )}
        </div>
      )}

      {git?.is_repo && !git.clean && (
        <UncommittedPanel git={git} onChanged={refreshVersions} />
      )}

      <h2>{locale === "zh-CN" ? "时间线" : "Timeline"}</h2>
      {timeline.length === 0 ? (
        <p className="dim">
          {locale === "zh-CN" ? "还没有版本记录。" : "No versions recorded yet."}
        </p>
      ) : (
        <table className="data">
          <thead>
            <tr>
              <th style={{ width: 80 }}>{locale === "zh-CN" ? "类型" : "Kind"}</th>
              <th>{locale === "zh-CN" ? "说明" : "Label"}</th>
              <th style={{ width: 150 }}>{locale === "zh-CN" ? "时间" : "When"}</th>
              <th style={{ width: 180 }} />
            </tr>
          </thead>
          <tbody>
            {timeline.map((entry) => (
              <TimelineRow
                key={`${entry.kind}-${entry.id}`}
                entry={entry}
                onCompare={() => void runCompare(entry.id, "current")}
                onRestore={async () => {
                  if (
                    !window.confirm(
                      locale === "zh-CN"
                        ? "回滚会覆盖当前章节内容（会先自动创建一个安全快照）。继续？"
                        : "Restoring overwrites the current section text. A safety snapshot is taken first. Continue?",
                    )
                  )
                    return;
                  try {
                    const result = await endpoints.versions.restore(project.id, entry.id);
                    notify({
                      kind: "success",
                      message: locale === "zh-CN" ? "已回滚" : "Restored",
                      detail: String(result.note ?? ""),
                    });
                    await useStore.getState().reloadDocument();
                    await loadTimeline();
                  } catch (error) {
                    reportError(error, "restoring the version");
                  }
                }}
              />
            ))}
          </tbody>
        </table>
      )}

      {diff && compare && (
        <DiffPanel
          diff={diff}
          left={compare.left}
          right={compare.right}
          onClose={() => {
            setDiff(null);
            setCompare(null);
          }}
        />
      )}
    </div>
  );
}

function RemoteGitCard({
  project,
  git,
  onChanged,
}: {
  project: Project;
  git: GitStatus;
  onChanged: () => Promise<void>;
}) {
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const [remotes, setRemotes] = useState<GitRemote[]>([]);
  const [remoteName, setRemoteName] = useState("origin");
  const [remoteUrl, setRemoteUrl] = useState("");
  const [sync, setSync] = useState<GitRemoteSync | null>(null);
  const [setupOpen, setSetupOpen] = useState(Boolean(git.has_remote));
  const [busy, setBusy] = useState<"configure" | "remove" | "fetch" | "pull" | "push" | null>(null);

  const loadRemotes = useCallback(async () => {
    try {
      const result = await endpoints.versions.remotes(project.id);
      setRemotes(result.remotes);
      setRemoteName((previous) => {
        if (result.remotes.some((item) => item.name === previous)) return previous;
        return result.remotes.find((item) => item.name === "origin")?.name ?? "origin";
      });
      if (result.remotes.length === 0) setSync(null);
      else setSetupOpen(true);
    } catch (error) {
      reportError(error, "loading Git remotes");
    }
  }, [project.id, reportError]);

  useEffect(() => {
    void loadRemotes();
  }, [loadRemotes, git.has_remote]);

  const selectedRemote = remotes.find((item) => item.name === remoteName);

  async function configureRemote() {
    const name = remoteName.trim();
    const url = remoteUrl.trim();
    if (!name || !url) {
      notify({
        kind: "warning",
        message:
          locale === "zh-CN"
            ? "请填写 remote 名称和地址"
            : "Enter both a remote name and URL",
      });
      return;
    }
    setBusy("configure");
    try {
      const result = await endpoints.versions.setRemote(project.id, url, name);
      setRemoteUrl("");
      setSync(null);
      await loadRemotes();
      await onChanged();
      notify({
        kind: "success",
        message: selectedRemote
          ? locale === "zh-CN" ? "远程地址已更新" : "Remote URL updated"
          : locale === "zh-CN" ? "远程仓库已配置" : "Remote configured",
        detail: `${String(result.remote ?? name)} · ${String(result.url ?? "")}`,
      });
    } catch (error) {
      reportError(error, "configuring the Git remote");
    } finally {
      setBusy(null);
    }
  }

  async function removeRemote() {
    if (!selectedRemote) return;
    const confirmed = window.confirm(
      locale === "zh-CN"
        ? `移除远程“${selectedRemote.name}”？本地提交和分支都会保留，只删除该项目 .git 中的远程地址。`
        : `Remove remote "${selectedRemote.name}"? Local commits and branches are kept; only its URL is removed from this project's .git.`,
    );
    if (!confirmed) return;
    setBusy("remove");
    try {
      await endpoints.versions.removeRemote(project.id, selectedRemote.name);
      setSync(null);
      setRemoteUrl("");
      await loadRemotes();
      await onChanged();
      setSetupOpen(false);
      notify({
        kind: "success",
        message: locale === "zh-CN" ? "远程已移除，本地历史已保留" : "Remote removed; local history preserved",
      });
    } catch (error) {
      reportError(error, "removing the Git remote");
    } finally {
      setBusy(null);
    }
  }

  async function fetchRemote(showToast = true) {
    setBusy("fetch");
    try {
      const result = await endpoints.versions.fetchRemote(project.id, remoteName);
      setSync(result.sync);
      await onChanged();
      if (showToast) {
        notify({
          kind: result.sync.diverged ? "warning" : "success",
          message: locale === "zh-CN" ? "远程状态已刷新" : "Remote state refreshed",
          detail: remoteSyncLabel(result.sync, locale),
        });
      }
      return result.sync;
    } catch (error) {
      reportError(error, "fetching the Git remote");
      return null;
    } finally {
      setBusy(null);
    }
  }

  async function pullRemote() {
    const confirmed = window.confirm(
      locale === "zh-CN"
        ? "从远程拉取论文？系统只接受干净工作区上的快进更新；真正替换文件前会保存数据库快照和磁盘手稿备份。历史分叉时会拒绝，不会自动合并。"
        : "Pull remote work? PaperCreator only accepts a fast-forward on a clean tree. It saves a database snapshot and disk-manuscript backup before replacing files, and refuses divergent history instead of auto-merging.",
    );
    if (!confirmed) return;
    setBusy("pull");
    try {
      const result = await endpoints.versions.pull(project.id, remoteName);
      if (result.updated) {
        await useStore.getState().reloadDocument();
        await onChanged();
      }
      if (result.sync) setSync(result.sync as GitRemoteSync);
      notify({
        kind: result.updated ? "success" : "info",
        message: result.updated
          ? locale === "zh-CN" ? "远程论文已安全快进" : "Remote work fast-forwarded safely"
          : locale === "zh-CN" ? "没有需要拉取的更新" : "No remote update was applied",
        detail: result.updated
          ? locale === "zh-CN"
            ? `已应用 ${String(result.commits ?? 0)} 个提交，并重载编辑器`
            : `Applied ${String(result.commits ?? 0)} commit(s) and reloaded the editor`
          : String(result.reason ?? ""),
      });
    } catch (error) {
      reportError(error, "pulling the Git remote");
    } finally {
      setBusy(null);
    }
  }

  async function pushRemote() {
    setBusy("push");
    try {
      const result = await endpoints.versions.push(project.id, remoteName);
      const refreshed = await endpoints.versions.fetchRemote(project.id, remoteName);
      setSync(refreshed.sync);
      await onChanged();
      notify({
        kind: "success",
        message: locale === "zh-CN" ? "本地提交已推送" : "Local commits pushed",
        detail: `${String(result.remote ?? remoteName)}/${String(result.branch ?? git.branch ?? "")}`,
      });
    } catch (error) {
      reportError(error, "pushing the Git remote");
    } finally {
      setBusy(null);
    }
  }

  if (!setupOpen && remotes.length === 0) {
    return (
      <div className="card" aria-label={locale === "zh-CN" ? "可选远程 Git" : "Optional Remote Git"}>
        <div className="row wrap">
          <div className="grow">
            <h3>{locale === "zh-CN" ? "可选：连接 GitHub / GitLab" : "Optional: connect GitHub / GitLab"}</h3>
            <p className="hint">
              {locale === "zh-CN"
                ? "当前所有版本仅保存在本机。添加 remote 后才允许手动同步；PaperCreator 永不自动推送。"
                : "All versions currently stay on this computer. Manual sync is enabled only after adding a remote; PaperCreator never auto-pushes."}
            </p>
          </div>
          <button className="btn" onClick={() => setSetupOpen(true)}>
            {locale === "zh-CN" ? "添加远程仓库" : "Add remote repository"}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="card" aria-label={locale === "zh-CN" ? "远程 Git" : "Remote Git"}>
      <div className="row wrap">
        <h3 className="grow">{locale === "zh-CN" ? "远程协作（显式启用）" : "Remote collaboration (explicit opt-in)"}</h3>
        {remotes.length === 0 && (
          <button className="btn sm" onClick={() => setSetupOpen(false)} disabled={busy !== null}>
            {locale === "zh-CN" ? "取消" : "Cancel"}
          </button>
        )}
      </div>
      <p className="hint">
        {locale === "zh-CN"
          ? "添加地址本身不会上传任何内容。Fetch 只刷新状态；Pull 只做有恢复材料的安全快进；Push 必须手动点击且永不强推。认证请使用 Windows Git Credential Manager 或 SSH key。"
          : "Adding a URL uploads nothing. Fetch only refreshes state; Pull only performs a recoverable fast-forward; Push requires an explicit click and never forces. Use Git Credential Manager or an SSH key."}
      </p>
      {selectedRemote && (
        <div className="row wrap" style={{ marginTop: 10 }}>
          <span className="chip ok">{selectedRemote.name}</span>
          <span className="dim mono" style={{ overflowWrap: "anywhere" }}>
            {selectedRemote.fetch}
          </span>
        </div>
      )}
      {sync && (
        <div className="row wrap" style={{ marginTop: 10 }} aria-label={locale === "zh-CN" ? "远程同步状态" : "Remote sync status"}>
          <span className={`chip ${sync.diverged ? "err" : sync.behind ? "warn" : "ok"}`}>
            {remoteSyncLabel(sync, locale)}
          </span>
          <span className="chip">↑ {sync.ahead}</span>
          <span className="chip">↓ {sync.behind}</span>
        </div>
      )}
      <div className="row wrap" style={{ marginTop: 10 }}>
        <input
          style={{ width: 130 }}
          aria-label={locale === "zh-CN" ? "Git remote 名称" : "Git remote name"}
          value={remoteName}
          onChange={(event) => {
            setRemoteName(event.target.value);
            setSync(null);
          }}
          placeholder="origin"
        />
        <input
          className="grow"
          aria-label={locale === "zh-CN" ? "Git remote 地址" : "Git remote URL"}
          value={remoteUrl}
          onChange={(event) => setRemoteUrl(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") void configureRemote();
          }}
          placeholder={
            selectedRemote
              ? locale === "zh-CN" ? "输入新地址以更新（留空不会修改）" : "Enter a new URL to update"
              : "https://github.com/you/paper.git"
          }
        />
        <button className="btn" onClick={() => void configureRemote()} disabled={busy !== null}>
          {selectedRemote
            ? locale === "zh-CN" ? "更新地址" : "Update URL"
            : locale === "zh-CN" ? "配置" : "Configure"}
        </button>
        {selectedRemote && (
          <button className="btn danger" onClick={() => void removeRemote()} disabled={busy !== null}>
            {locale === "zh-CN" ? "移除远程" : "Remove remote"}
          </button>
        )}
      </div>
      <div className="row wrap" style={{ marginTop: 10 }}>
        <button
          className="btn"
          onClick={() => void fetchRemote()}
          disabled={!selectedRemote || busy !== null}
        >
          Fetch
        </button>
        <button
          className="btn"
          onClick={() => void pullRemote()}
          disabled={!selectedRemote || busy !== null}
        >
          Pull (ff-only)
        </button>
        <button
          className="btn primary"
          onClick={() => void pushRemote()}
          disabled={!selectedRemote || busy !== null}
        >
          Push
        </button>
        <span className="hint">
          {locale === "zh-CN"
            ? "分叉历史会停下并交给外部 Git 客户端处理。"
            : "Diverged history stops here for resolution in an external Git client."}
        </span>
      </div>
    </div>
  );
}

function remoteSyncLabel(sync: GitRemoteSync, locale: string): string {
  const labels: Record<GitRemoteSync["state"], [string, string]> = {
    unpublished: ["远端无此分支", "remote branch unpublished"],
    up_to_date: ["已同步", "up to date"],
    ahead: ["本地领先", "local ahead"],
    behind: ["远端有更新", "remote ahead"],
    diverged: ["历史已分叉", "history diverged"],
  };
  return labels[sync.state][locale === "zh-CN" ? 0 : 1];
}

function TimelineRow({
  entry,
  onCompare,
  onRestore,
}: {
  entry: TimelineEntry;
  onCompare: () => void;
  onRestore: () => void;
}) {
  const locale = useStore((s) => s.locale);
  return (
    <tr>
      <td>
        <span className={`chip ${entry.kind === "commit" ? "" : "ok"}`}>
          {entry.kind === "commit" ? "git" : "snapshot"}
        </span>
      </td>
      <td>
        <div>
          {entry.label}
          {entry.auto && (
            <span className="chip dim" style={{ marginLeft: 6 }}>
              auto
            </span>
          )}
        </div>
        <div className="dim" style={{ fontSize: "var(--fs-xs)" }}>
          <span className="mono">{entry.short}</span>
          {entry.detail ? ` · ${entry.detail}` : ""}
          {entry.author ? ` · ${entry.author}` : ""}
        </div>
      </td>
      <td className="dim">{entry.timestamp.slice(0, 16).replace("T", " ")}</td>
      <td>
        <button className="btn sm" onClick={onCompare}>
          {locale === "zh-CN" ? "与当前对比" : "Diff vs current"}
        </button>{" "}
        <button className="btn sm danger" onClick={onRestore}>
          {locale === "zh-CN" ? "回滚" : "Restore"}
        </button>
      </td>
    </tr>
  );
}

function UncommittedPanel({
  git,
  onChanged,
}: {
  git: GitStatus;
  onChanged: () => Promise<void>;
}) {
  const project = useStore((s) => s.project)!;
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const [diff, setDiff] = useState("");
  const [busy, setBusy] = useState(false);

  const tracked = Array.from(
    new Set([...(git.staged ?? []), ...(git.unstaged ?? [])].map((item) => item.path)),
  );
  const untracked = git.untracked ?? [];

  useEffect(() => {
    void endpoints.versions
      .diff(project.id)
      .then((result) => setDiff(result.diff))
      .catch(() => setDiff(""));
  }, [project.id, git.staged, git.unstaged]);

  async function discardTracked() {
    if (tracked.length === 0) return;
    const preview = tracked.slice(0, 8).map((path) => `• ${path}`).join("\n");
    const extra = tracked.length > 8 ? `\n… +${tracked.length - 8}` : "";
    const keptPreview = untracked.slice(0, 8).map((path) => `• ${path}`).join("\n");
    const keptExtra = untracked.length > 8 ? `\n… +${untracked.length - 8}` : "";
    const confirmed = window.confirm(
      locale === "zh-CN"
        ? `确定放弃 ${tracked.length} 个已跟踪文件的修改吗？\n\n${preview}${extra}\n\n以下未跟踪文件会保留：\n${keptPreview || "（无）"}${keptExtra}\n\n操作前会在项目 .papercreator/conflicts/ 中创建快照、手稿备份和 Git 二进制补丁。`
        : `Discard changes in ${tracked.length} tracked file(s)?\n\n${preview}${extra}\n\nThese untracked files will be kept:\n${keptPreview || "(none)"}${keptExtra}\n\nBefore restoring Git files, PaperCreator creates a snapshot, manuscript backup, and binary patch under the project's .papercreator/conflicts/.`,
    );
    if (!confirmed) return;
    setBusy(true);
    try {
      const result = await endpoints.versions.discard(project.id, true);
      await useStore.getState().reloadDocument();
      await onChanged();
      const patch = String((result.git_patch as Record<string, unknown> | undefined)?.path ?? "");
      const backup = String(
        (result.manuscript_backup as Record<string, unknown> | undefined)?.path ?? "",
      );
      notify({
        kind: "success",
        message:
          locale === "zh-CN" ? "已安全放弃已跟踪修改" : "Tracked changes safely discarded",
        detail:
          locale === "zh-CN"
            ? `未跟踪文件已保留。恢复补丁：${patch}；手稿备份：${backup}`
            : `Untracked files were kept. Recovery patch: ${patch}; manuscript backup: ${backup}`,
      });
    } catch (error) {
      reportError(error, "discarding tracked Git changes");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="card"
      aria-label={locale === "zh-CN" ? "未提交的修改" : "Uncommitted changes"}
    >
      <h3>{locale === "zh-CN" ? "未提交的改动" : "Uncommitted changes"}</h3>
      <p className="hint">
        {locale === "zh-CN"
          ? `${tracked.length} 个已跟踪文件有修改；${untracked.length} 个未跟踪文件会被保留。`
          : `${tracked.length} tracked file(s) changed; ${untracked.length} untracked file(s) will be kept.`}
      </p>
      {untracked.length > 0 && (
        <p className="dim mono" style={{ margin: "8px 0" }}>
          {locale === "zh-CN" ? "未跟踪：" : "Untracked: "}
          {untracked.join(", ")}
        </p>
      )}
      {diff && (
        <div className="diff" style={{ maxHeight: 300, overflow: "auto" }}>
          {diff.split("\n").map((line, index) => (
            <div
              key={index}
              className={
                line.startsWith("+") && !line.startsWith("+++")
                  ? "add"
                  : line.startsWith("-") && !line.startsWith("---")
                    ? "del"
                    : line.startsWith("@@")
                      ? "hunk"
                      : ""
              }
            >
              {line}
            </div>
          ))}
        </div>
      )}
      {tracked.length > 0 && (
        <div className="row" style={{ marginTop: 12 }}>
          <span className="hint grow">
            {locale === "zh-CN"
              ? "这是危险操作，但恢复材料会先写入当前项目的 .papercreator/conflicts/。"
              : "This is destructive, but recovery material is written to this project's .papercreator/conflicts/ first."}
          </span>
          <button className="btn danger" onClick={() => void discardTracked()} disabled={busy}>
            {locale === "zh-CN" ? "放弃已跟踪修改" : "Discard tracked changes"}
          </button>
        </div>
      )}
    </div>
  );
}

function DiffPanel({
  diff,
  left,
  right,
  onClose,
}: {
  diff: Record<string, any>;
  left: string;
  right: string;
  onClose: () => void;
}) {
  const locale = useStore((s) => s.locale);
  const isSnapshotDiff = diff.mode === "snapshot";

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <header>
          <span>
            {locale === "zh-CN" ? "版本对比" : "Compare"} · {left.slice(0, 10)} →{" "}
            {right === "current" ? (locale === "zh-CN" ? "当前" : "current") : right.slice(0, 10)}
          </span>
          <button className="btn icon sm" onClick={onClose} aria-label={locale === "zh-CN" ? "关闭版本对比" : "Close comparison"}>
            ✕
          </button>
        </header>
        <div className="modal-body">
          <div className="row wrap" style={{ marginBottom: 10 }}>
            <span className="chip">{isSnapshotDiff ? "snapshot diff" : "git diff"}</span>
            {isSnapshotDiff &&
              Object.entries(diff.summary as Record<string, number>).map(([key, value]) => (
                <span
                  key={key}
                  className={`chip ${key === "added" ? "ok" : key === "removed" ? "err" : ""}`}
                >
                  {locale === "zh-CN"
                    ? ({ added: "新增", removed: "删除", changed: "修改", unchanged: "未变化" } as Record<string, string>)[key] ?? key
                    : key} {value}
                </span>
              ))}
          </div>
          {diff.note && <p className="dim">{String(diff.note)}</p>}

          {isSnapshotDiff ? (
            (diff.sections as any[])
              .filter((section) => section.status !== "unchanged")
              .map((section) => (
                <div key={section.key} style={{ marginBottom: 16 }}>
                  <h3>
                    {section.key}{" "}
                    <span className={`chip ${section.status === "added" ? "ok" : "warn"}`}>
                      {locale === "zh-CN"
                        ? ({ added: "新增", removed: "删除", changed: "修改" } as Record<string, string>)[section.status] ?? section.status
                        : section.status}
                    </span>
                    <span className="dim" style={{ fontWeight: 400, marginLeft: 8 }}>
                      {section.words_before} → {section.words_after}{" "}
                      {locale === "zh-CN" ? "词" : "words"}
                    </span>
                  </h3>
                  <div className="diff" style={{ maxHeight: 260, overflow: "auto" }}>
                    {String(section.diff)
                      .split("\n")
                      .map((line, index) => (
                        <div
                          key={index}
                          className={
                            line.startsWith("+") && !line.startsWith("+++")
                              ? "add"
                              : line.startsWith("-") && !line.startsWith("---")
                                ? "del"
                                : line.startsWith("@@")
                                  ? "hunk"
                                  : ""
                          }
                        >
                          {line}
                        </div>
                      ))}
                  </div>
                </div>
              ))
          ) : (
            <div className="diff">
              {String(diff.diff ?? "")
                .split("\n")
                .map((line, index) => (
                  <div
                    key={index}
                    className={
                      line.startsWith("+") && !line.startsWith("+++")
                        ? "add"
                        : line.startsWith("-") && !line.startsWith("---")
                          ? "del"
                          : line.startsWith("@@")
                            ? "hunk"
                            : ""
                    }
                  >
                    {line}
                  </div>
                ))}
            </div>
          )}
          {isSnapshotDiff &&
            (diff.sections as any[]).every((section) => section.status === "unchanged") && (
              <p className="dim">
                {locale === "zh-CN" ? "两个版本内容相同。" : "The two versions are identical."}
              </p>
            )}
        </div>
      </div>
    </div>
  );
}

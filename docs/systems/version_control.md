> 文档用途：项目级 Git 与数据库快照双轨版本  
> 最后检查：2026-07-28  
> 对应代码：`backend/papercreator/vcs/`、`store/snapshots.py`  
> 文档状态：部分可用

# 版本控制

每个 PaperCreator 新论文项目对应 `<workbench>/.papercreator/projects/<slug>` 和默认独立的本地 Git repo。Git 版本化可读的 manuscript/metadata/bibliography 文件；项目 `.papercreator/`（同步基线、恢复副本、项目 Skill）按默认 `.gitignore` 留在本地，靠完整项目/工作台备份保护。SQLite snapshots 保存章节内容和元数据，即使 Git 禁用也可比较/恢复。`vcs/versions.py` 组合两者为统一时间线。项目 Git 与源码根 Git 是两个互不影响的仓库；本地 commit 不代表 push，也不会访问网络。

Git 封装覆盖 init/status/commit/log/diff/branches/checkout/remotes/fetch/fast-forward/push/remove/discard，所有 cwd 必须位于当前 projects 根，关闭交互式 credential prompt，并使用 repo-local fallback identity，不修改全局 Git 身份。手动 init 成功后会持久化项目 `git_enabled=true`，使后续统一 Save version 同时生成 commit 和 snapshot。status 使用 `--untracked-files=all` 返回实际文件而非折叠目录。checkout/restore/discard/pull 在改动权威侧前检查手稿同步状态。

Remote URL 不再从 `git remote -v` 按空白切分，而是逐 remote 调用 `get-url`，因此中文/空格本地路径能完整往返。HTTPS URL 中的内嵌 password/token 仍可由 Git 自身存于项目 `.git/config`，但 PaperCreator 的返回值、错误命令和 stdout/stderr 会先脱敏，不向 API/UI/日志回显。push 永不 force，且关闭终端 prompt；本地 bare remote 测试已证明首次 push 成功，远端分叉后的 non-fast-forward 会被拒绝且本地提交/文件不变。

Versions UI 把“本地版本控制（默认）”与“远程协作（显式启用）”分成两层。没有 remote 时只显示本地 commit/branch/diff/restore 和折叠的“添加 GitHub/GitLab”入口，不显示可用同步动作；添加命名 remote 本身不会上传内容，之后才允许手动 Fetch、Pull (ff-only) 与 Push，且永不自动 push。Fetch 只更新 remote-tracking refs，返回 `unpublished|up_to_date|ahead|behind|diverged` 和 ahead/behind 数量，不碰本地文件。Pull 先 fetch，只接受当前非 detached 分支、干净工作树和 `ahead=0, behind>0`；真正快进前要求 DB 可由磁盘重建，再创建 DB snapshot 和磁盘手稿备份，`git merge --ff-only` 成功后 reindex DB。脏树、远端无分支、local-ahead 和分叉均不自动合并；其中分叉明确 409，交给外部 Git 客户端解决。移除 remote 只删除项目 `.git/config` 中对应连接，本地 commits/branches/工作文件全部保留。

确认执行 discard 时的顺序是：确认 DB 可从文件替换 → DB snapshot → 复制当前磁盘手稿 → `git diff --binary HEAD` 写入项目 `.papercreator/conflicts/` → 只还原 tracked 文件（untracked 保留）→ reindex DB → 更新同步基线。binary patch 直接由 Git 写文件，不经过日志/响应正文；API 只返回路径、大小和恢复提示。任何预检失败都发生在 Git 修改工作树之前。

`save` 可同时建 snapshot 和 commit。Versions UI 显示完整 staged/unstaged/untracked 状态（不把 timeline 摘要当完整 status），提供可审计的 discard confirm、探索分支创建和 checkout。Agent 运行有 pre/post snapshot。Git checkout 后必须把磁盘重新索引到 DB；普通编辑/commit 前必须 flush DB 到磁盘。同步层以 `.papercreator/manuscript-sync.json` 的 DB/磁盘双摘要为共同基线，拒绝未经确认覆盖另一侧的新修改。

真实 Electron E2E 已验证从禁用 Git 的项目手动启用本地 Git、双版本保存、tracked+untracked 混合状态、可恢复 discard、探索分支 commit、main↔分支↔main，以及默认不显示同步按钮 → 显式展开远端 → 中文/空格 bare remote 配置 → 首次 push → 协作者 clone/commit → fetch 不改手稿 → pull recovery/reindex → 再次 push；随后双方各提交 1 次，UI 显示 ↑1/↓1/diverged，Pull 受控 409 且两侧独有文件均未被自动混合。最后从 UI 移除 remote，并以 `rev-parse HEAD` 验证本地提交未变化、Git 仍可用。6 个模块合同另覆盖凭据不回显、脏树拒绝、remote removal 和分叉/non-fast-forward 不改本地文件；API 合同验证 pull snapshot/backup/DB reindex 与 DELETE remote 保留 HEAD。当前源码根目录本身不是 Git repo，这与“项目级 Git 功能可用”是两件事。仍未验收 GitHub/GitLab HTTPS/SSH/Credential Manager 的真实认证、复杂分叉解决、detached HEAD 和多人并发；产品当前刻意不提供自动 merge。

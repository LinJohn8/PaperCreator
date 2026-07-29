> 文档用途：当前代码对应的故障排查手册  
> 最后检查：2026-07-28

# 故障排查

## Electron 显示后端未响应

### 现象
启动 90 秒后失败、状态栏离线或 Output 有 backend error。

### 可能原因
开发态解释器/依赖缺失、安装态 bundled exe 缺失/被杀软隔离、8765 冲突、工作台不可写、配置/DB 错误。

### 检查方法
开发态运行 `.\.venv\Scripts\python.exe -m papercreator --check`；安装态查看 `<workbench>/.papercreator/logs/desktop.log` 给出的 executable 路径并运行其 `--check`。再看 `errors.log` 和 `/api/system/health`。

### 解决方法
开发仓运行 `npm run setup`；确认 `PC_PYTHON/PC_PORT` 一致；停止已确认的错误占用者。安装态不使用 `PC_PYTHON`；若资源中的后端缺失，应重新安装并检查杀软记录，不能让产品偷偷回退系统 Python。

### 是否影响数据
诊断/重启不影响；不要删除 home。

## `npm run setup` 找不到 Python

确认 Python 3.10+ 在 PATH，或先设置 `PC_PYTHON` 供 Electron。2026-07-27 已修复 Node `shell:true` 的误探测；若仍发生，运行 `python -c "import sys; print(sys.executable,sys.version)"` 并检查脚本版本。

## 端口被占用

Electron 会复用健康 PaperCreator。若响应不是本项目，修改 `.env` 的 `PC_PORT` 并重启；不要直接 taskkill 未确认 PID。无数据影响。

## 前端修改未生效

开发确认 Vite 5173；生产 Electron 读取安装包 ASAR，需 `npm run package` 并启动新的 win-unpacked/安装版本。当前 Chromium cache 位于所选 `.papercreator/electron`；不要先删整个工作台。无业务数据影响。

## 首次没有出现工作台选择或打开了错误工作台

显式 `PAPERCREATOR_WORKBENCH`、开发态仓库默认和 AppData 的 `workbench-location.json` 按顺序决定位置。安装态从菜单切换工作台并重启；确认 `GET /api/workbench` 的 `workbench` 与 `managed_directory`。不要手改 DB 绝对路径或删除旧工作台；切换不等于迁移。

## 分类导入很慢或磁盘不足

### 现象

代码/数据集/补充材料/待分类目录导入长时间运行、进度停滞，或弹窗/Output → Jobs 显示失败、取消及机器错误码。

### 可能原因

- `resource_import_insufficient_space`：目标工作台所在卷未满足源字节数加安全余量。
- `resource_import_source_changed`：预扫描后文件/空目录被修改、替换、删除，resolved path 逃离源根，或节点变成 link/reparse point。
- `resource_import_link_root`：所选根本身是 symlink、junction 或 Windows reparse point；嵌套 link/reparse 会被安全排除而非跟随。
- `resource_import_cleanup_failed`：杀软、索引器或其他进程占用 reserved staging，使失败/取消后的清理未完成。
- 大量小文件、网络盘或慢盘本身吞吐低；代码项目会排除常见依赖/构建目录和 `.env*`（保留 `.env.example`），数据集不会套用这些代码排除规则。

### 检查方法

在导入弹窗或 Output → Jobs 查看百分比、阶段消息、Job id、error code 和 hint；再看 `logs/errors.log`。确认目标卷可用空间、源仍稳定且所选路径不是链接。若 UI 曾重连，Job 状态仍以 `/api/system/jobs/{id}` 的 durable 记录为准，不以漏掉的一条 SSE 判断。

### 解决方法

空间不足时清理目标卷或选择更大卷上的工作台。源变化时停止同步器/构建过程后重新导入。可用“取消导入”等待 cooperative cleanup，或“转入后台”继续；不要为停止任务强杀进程。若明确报 `resource_import_cleanup_failed`，先关闭占用该提示路径的进程，再仅处理错误详情给出的严格 reserved `.partial-res_<16hex>`；不要批量删除分类目录，也不要删除 `.partial-user-data` 等相似名称。异常退出留下的严格 staging 会在下次启动自动尝试回收。

### 是否影响数据

正常失败/取消不产生 ready 资源，已完成资源和源目录不受影响。成功以原子 rename 后的托管副本为准，DB 是最后登记点。清理失败只留下不可用 staging；手工误删非 reserved 目录可能造成不可恢复的数据丢失。

### 相关文件

`store/resources.py`、`api/routes/workbench.py`、`api/app.py`、`WorkbenchPanel.tsx`、`api/events.ts`、`OutputPanel.tsx`。

## API 404/422/500

- 404：确认 [api_reference.md](api_reference.md) 路径和 id；静态路由应在动态路由前。
- 422：查看 response `detail` 或 AppError；对照 `/api/docs` schema。
- 500：看 errors.log；不应通过重试掩盖稳定复现 bug。

本轮修复过 `/api/export/convert` 被 `/{project_id}` 抢占和 health 导入遮蔽；回归测试已覆盖。

## 检索无结果或很慢

查看 `/api/search/providers` 和每源 stats。arXiv 最少 3.1s 间隔，Semantic Scholar 无 key 易 429，local 需要 imports 文件。配置 contact email/key，减少 query variants/limit，保留其他 Provider。清 HTTP cache 只在确认缓存损坏/陈旧时做。

## 模型下载/分析失败

Settings → Analysis 查看 blocker；可设 `PC_HF_ENDPOINT`、预下载模型或 `PC_OFFLINE_MODELS=1`。TF-IDF/PCA 回退应仍产图。不要把不同 embedding backend 的坐标直接比较。

## LLM/Agent 配置错误、限流、超时或断流

### 现象

Provider 测试或 Agent run 显示 `authentication_error`、`configuration_error`、`rate_limited`、`timeout`、`network_error`、`invalid_response`、`stream_interrupted` 等 outcome；run、step 和 job 都应为 failed。Writer 已出现少量文本后断流时，审计窗口可能包含 partial output，但完整手稿保持运行前版本。

### 可能原因

- 401/403：密钥错误、权限或 endpoint/provider 不匹配。
- 429：额度、并发或速率限制；服务可能返回 `retry_after_s`。
- timeout/network：代理、DNS、TLS、endpoint 不可达或模型响应过慢。
- invalid/empty/truncated/stream interruption：上游响应缺字段、坏 SSE/NDJSON、缺少协议 terminal event、连接在首个 delta 后中断，或模型因长度截断输出。
- configuration/unavailable：没有可用模型、base URL 不合法，或 Ollama 等本地服务未启动。

### 检查方法

先打开 Settings → Models 测试对应 Provider，再在 Agents 的 run detail 查看 outcome、error code、provider/model、HTTP status、retry-after、context/hint、pre/post snapshot 和失败 step audit。必要时检查 `<workbench>/.papercreator/logs/errors.log`；不要在截图或问题报告中附带密钥。`llm_usage` 会记录失败调用，预算高于可见成功正文并不一定是重复计费错误。

### 解决方法

配置/认证错误先进入模型设置修正，不要盲目重试。429 按 `retry_after_s` 等待并降低并发或更换模型；timeout/network 先验证 endpoint 与网络。可恢复故障使用“重试相同运行”，它会从持久化 request 重建 paper、Skill、roles 和 config。若不接受本次修改，先比较 pre/post snapshot，再显式恢复运行前快照；不要手删 DB、run、step 或 usage。收到首个流式 delta 后系统不会自动重放，以避免重复正文。

### 是否影响数据

失败 step、诊断、partial output、usage 和双快照会追加保存；partial output 不会写成完整章节。恢复快照会覆盖当前手稿，因此先比较差异，并避免与其他编辑同时进行。

### 相关文件

`core/errors.py`、`core/jobs.py`、`llm/backends.py`、`llm/client.py`、`agents/base.py`、`agents/orchestrator.py`、`store/runs.py`、`AgentsView.tsx`、`SettingsView.tsx`。

## Skill 未发现

目录必须是 `<root>/<id>/SKILL.md`，检查 UTF-8/frontmatter/必需 instructions；点击 sync 并看 warning。builtin 只读，同 id user/project 会覆盖 earlier scope。

## Git/版本失败

确认项目路径在 `<workbench>/.papercreator/projects`、Git 在 PATH、项目已 init、Credential Manager/SSH 可非交互使用。Remote Fetch 只更新 tracking refs；Pull 要求当前分支和干净工作树，只接受 fast-forward。若显示 `diverged`/409，PaperCreator 不会自动 merge：在外部 Git 客户端显式解决并确认手稿后，再回到 Versions 执行 Fetch。checkout/restore/pull 后由服务 reindex；commit 前 flush。确认 discard 后会在项目 `.papercreator/conflicts/` 留下磁盘副本和 `tracked-changes.patch`，Remote Pull 更新前也会留下手稿备份和 DB snapshot。需要恢复时先停止继续编辑并检查对应时间戳目录。根源码 Git 与每个论文项目自己的 Git 是两个独立仓库。

## 手稿提示“数据库与文件冲突”

### 现象

Editor 顶部出现同步横幅；保存/flush/reindex、checkout、restore、Agent 或 Overleaf 返回 HTTP 409，错误码 `manuscript_sync_conflict`。

### 可能原因

用户或 Git 修改了 `manuscript/NN-key.md|tex`，同时 UI/Agent 侧数据库也有新内容；两侧改了同一章节或有增删/重命名；旧 schema v1/无基线项目第一次启用时两侧已不同；或 `.papercreator/manuscript-sync.json` 损坏/缺失。schema v2 可识别两侧修改章节集合，但不会猜测同节内容。`full.md`/`full.tex` 是派生预览，不是可回导来源。

### 检查方法

先停止继续写入。查看横幅和 `GET /api/writing/{project_id}/sync-status` 的 `state`、`db_changed_sections`、`disk_changed_sections`、`merge_allowed/merge_blockers`、文件清单与 `baseline_error`；用“查看文件”检查磁盘内容，并从 UI/快照查看 DB 内容。不要通过删除基线消除告警。

### 解决方法

若横幅显示两侧只修改了不同既有章节，可确认“合并不同章节”；系统会先建 DB snapshot 和两侧镜像，预览后状态变化会拒绝并要求重试。否则确认正确来源后显式选择“以数据库为准”或“以文件为准”；系统把被覆盖侧存入 `.papercreator/conflicts/`，以文件为准另建 snapshot。同节两边都有需要的段落时先人工合并。完成应看到 `state=in_sync`。

### 是否影响数据

普通 409 不覆盖目标侧；但章节 PATCH 会保留刚提交到 DB 的文本，因此 409 不是 DB 回滚，通常会从 `disk_changed` 进入 `diverged`。强制选边会覆盖一侧，但有恢复副本。恢复副本未经人工确认不要清理。

### 相关文件

`store/documents.py`、`api/routes/writing.py`、`EditorView.tsx`、项目 `.papercreator/manuscript-sync.json` 与 `.papercreator/conflicts/`。

## PDF/Overleaf 失败

PDF 导出检查 TeX engine/bibtex PATH；中文需 XeLaTeX。没有 TeX 时上传生成的 Overleaf ZIP。扫描 PDF 导入先查看 `GET /api/writing/import/ocr-capabilities`：需要本机 Tesseract、所选语言包，以及 pypdfium2 或 pdftoppm；未安装时保留原 PDF 并返回 `requires_ocr`，不会联网或伪造文字。Overleaf Git Bridge 需要付费能力、URL/token/Git；pull apply 有损并会覆盖章节，先 snapshot。

## DB/数据格式问题

立即停止写入并备份完整 `.papercreator`；查看 `--check` 的 DB schema/stats 和 errors.log。当前 DB schema v6、workbench manifest schema v1、manuscript sync schema v2 是三个不同版本域。不要手工删除 WAL/SHM、重建空 DB 或批量 reindex。恢复前区分 DB 是最新还是磁盘是最新。

## 重装/重建后数据丢失

安装态检查当前选择的工作台父目录，而不是安装目录或 AppData。旧 `.papercreator` 不会被切换/卸载自动搬迁或删除；重新选择其父目录即可。headless/test 才检查 `PAPERCREATOR_HOME/WORKSPACE` override。当前无 Docker volume；不要套用容器删除命令。

> 文档用途：可从当前代码/本轮操作确认的内部变更记录  
> 最后检查：2026-07-29  
> 注意：2026-07-29 首次公开提交之前根目录没有 Git 历史，无法还原更早的提交日期/作者/差异；不可确认内容标“日期待确认”。

# 内部变更记录

## 2026-07-29

- 项目重新定位为实验性半成品和低优先级维护仓库。根 README 首屏明确无稳定性、兼容性、持续开发、论文质量或正式发行保证，并列出数据外发、AI 幻觉、旧安装包、外部服务和规模验收边界。
- 采用 MIT License（PaperCreator contributors），新增贡献说明、安全披露策略和 Windows GitHub Actions CI；包元数据指向 `LinJohn8/PaperCreator`。扩展忽略规则并在首次提交前按 Git 实际索引执行密钥、大文件与运行时数据审计。
- 用户已明确授权初始化根 Git、建立 `main`、添加 GitHub remote 并公开推送。首次公开提交之前的历史仍不可恢复或追溯。

## 2026-07-28

- 新增任务型“快速开始”：空工作台首启显示五步真实进度，步骤直达项目、资料、手稿和版本；可稍后关闭、以 `ui.quick_start_version` 持久关闭自动显示，并从 Help/命令面板随时重开。修复原生“新建项目”只导航不打开表单、Help“打开日志目录”无接收方、安装态故障页暴露源码命令三个产品入口缺陷；创建表单补齐字段关联、Escape 和忙碌保护，应用内模态框/命令面板统一约束 Tab 焦点，旧图标关闭入口补齐中英文名称。Electron 在 1365×900/1100×700 取证并验证菜单、日志 IPC、偏好与重启。
- 统一配置合同为 default < UI settings < secret file < `.env` < process environment；修复环境层伪称稀疏却携带默认值、JSON 空字符串无法清除旧值，以及 reload 不刷新 dotenv 的问题。新增无值 `GET /api/settings/sources` 来源诊断、Settings 页面精确 `.env`/process override 展示和逐层/脱敏/API 回归。
- AI 对话治理升至 DB v6：新增 `assistant_thread_imports`；JSON/JSON.GZ 归档通过受限 IPC 保存/选择，压缩前后 256 MiB 上限；preview→confirm 导入以来源线程 ID+完整内容 fingerprint 幂等，删除后可恢复，来源变化生成新副本且不覆盖本地历史。单条消息可经防并发 token 不可逆清理 content/actions/meta，只保留 SHA-256、大小、时间和原因。
- 手稿同步 manifest 升至 schema v2，保存逐章节 filename/双侧 fingerprint；仅对无增删/重命名、DB 与磁盘修改章节集合完全不重叠的状态提供显式合并，操作前建立 DB snapshot 和两侧镜像，token 陈旧/同节双改/v1 基线继续 409。真实 Electron 覆盖这一链。
- 新增可选本地扫描 PDF OCR：Tesseract + pypdfium2/pdftoppm 能力/语言包探测，只补无文字层页面，1–200 页、逐页超时，全程离线且不改原 PDF。当前机器无引擎，仅 mock 合同通过。DOCX OOXML 提取增加 part 大小边界、表格行、常见 OMML 线性公式、脚注/尾注和 exact golden。
- CLI 有效 `--host/--port` 现在进入 app factory、lifespan 日志、CORS 与开发热重载子进程；新增 CLI 回归。OpenAPI 当前为 14 router / 157 paths / 181 operations，并新增 method/path SHA-256 快照。
- E2E 新增默认 dry-run 的严格 Temp 清理器；失败关闭超时只对测试拥有的 Electron PID tree 强制兜底并等待退出。修正不同章节场景各自消费预期 409，以及 LaTeX 摘要引用应在完整 `main.tex + sections` 中验收。当前真实链 `1 passed (1.6m)`。

### 论文工作台交互、导入/翻译、AI 助手与提示词

- 启动恢复最后项目与界面语言；第二实例在窗口未就绪时安全等待，并让用户选择打开检索、切换工作台或取消。中文/英文不再在菜单、导航和通知中拼接显示；命令面板切换语言也写入工作台设置。检索源切换改为真实 provider id 列表，修复空 PATCH 的 `no settings were provided`；普通通知时长缩短，错误保留。
- 项目卡改为论文项目入口；创建/点击后进入手稿。章节支持新增、删除、双语改名、双目标、要求、排序和空手稿首节；DB v3 增加 `target_words_zh`。编辑器文章工具集中导入/导出/Overleaf/投稿模板/打开文件夹。
- 新增 PDF/DOCX/MD/TXT/TeX 提取。自有论文完整文本写可重建 cache sidecar；项目手稿使用 preview→apply、SHA-256 复核、append/replace、替换前 snapshot 和 `.papercreator/imports/` 审计副本。pypdf 先读文字层，扫描页只在用户显式选择且本机能力存在时做有界离线 OCR。
- 新增离线术语、MyMemory 公共翻译和已配置 LLM 翻译；单节、选词和批量流程均不自动覆盖，UI 展示隐私/费用边界。新增 11 个原创结构模板，并把内容结构与官方排版文件分离。
- 新增用户授权 venue ZIP 导入：拒绝路径穿越、盘符、符号链接、加密条目和超限 archive；复核源 SHA-256，写项目 assets 并以 manifest 记录来源、许可证与摘要。不批量复制许可证不清晰的 Overleaf Gallery 内容。
- 新增只读项目 AI chat 和可确认动作；聊天本身不写手稿、Skill、Git 或 remote。DB v4 保存提示词；DB v5 新增独立工作台/项目线程与严格有序消息；顶部新增项记录本轮 v6 归档来源映射、导入和脱敏。
- 完成 AI 对话治理：列表/范围返回消息、字符、估算字节与活动统计；版本化 JSON 导出经受限 Electron save IPC 原生保存/定位；保留期默认关闭；批量删除必须预览候选并以覆盖消息内容的 SHA-256 token 在同一写事务重算，变化即拒绝。项目/工作台严格隔离，项目删除级联线程与消息。
- 重构长文/批量翻译为 preview-first Job：MyMemory 外发必须显式确认，≤100k 字符/250 请求，句段稳定分块且代码围栏/展示公式/空白本地保留；限速并处理 Retry-After/timeout/network，支持取消和 durable progress。Job 不写章节；完成预览可恢复，apply 校验 source/paired SHA-256、创建快照并同事务一次写入，重复调用幂等。真实 Electron 证明预览阶段数据库不变。
- 新增章节 CRUD 桌面回归时修复两项真实问题：SectionDialog 的 label 现在通过 `htmlFor/id` 关联输入控件；`reloadDocument()` 在当前章节被删除/分支切换后会选择首个仍存在章节，并清理只属于已删除 key 的 dirty 草稿，避免编辑器空白。
- 修复 Windows 关闭/重启的根因：venv launcher 与真实 Python 可能是双进程，`SIGTERM` 会跳过 FastAPI lifespan，且仍打开的 Renderer SSE 会阻塞 Uvicorn。Electron 现为 owned backend 生成随机 256-bit capability，经隐藏回环路由请求正常退出；窗口关闭先断开 Renderer，backend-only restart 使用 2 秒 graceful connection timeout；lifespan 执行 WAL truncate checkpoint，Electron 等待退出码 0。`taskkill /T /F` 仅为精确 owned PID 超时兜底。
- E2E 临时工作台清理保持严格 temp/prefix 边界，对 Windows `EBUSY/EPERM/ENOTEMPTY` 做约 22 秒有界退避；应用退出/checkpoint 已显式断言后，Defender/索引器仍占用最后测试 DB 时只告警。它不扫描、删除或接触用户工作台。
- 修复 Electron 不可靠支持 `window.prompt()` 导致提示词变量/AI 本地提交说明无反应：变量改为应用内动态表单；本地 commit 改为应用内对话框，必须勾选“仅当前项目本地 Git、不会推送”才启用提交。Prompt/章节弹窗同时补齐 label/id 与关闭按钮无障碍名称。
- 设置语言 select 补齐 label/id；E2E 通用 launch 不再错误等待可能从未绘制的工作台首页，而等待非 transient shell，并由调用者分别验证首屏或恢复项目。Idea 重启持久化通过真实资源 API 验证，不要求在项目手稿页显示首页卡片。
- 新增退出前 Renderer 保存握手：原生 close 重定向到 `before-quit`，主进程通过窄 lifecycle IPC 等待 `saveAllSections()`；仍有 dirty 或超时就取消退出并保留窗口/后端，保存成功才关闭 SSE/后端。E2E 故意不按保存直接退出，第四次启动确认正文恢复。
- 本轮源码 OpenAPI 为 14 router / 157 paths / 181 operations，另有 1 个隐藏关机路由不进入 schema。非联网后端 `357 passed, 3 deselected in 28.76s`（360 collected），TypeScript 与 95-module Vite build 通过；真实 Electron 长链 `1 passed (1.6m)`。没有重打 EXE/NSIS，旧安装包不含本轮新增功能。

### VS Code 式单行标题栏、本地 Git 默认层与可选远程协作

- Windows 窗口改为 Electron `titleBarStyle=hidden` + 原生 `titleBarOverlay`：保留最小化/最大化/关闭、Snap Layout 和系统无障碍行为，同时把品牌图标、File/Edit/View/Help、项目身份、命令入口和连接状态合并为一行。图标位于 File 左侧，应用动作位于右侧原生窗口按钮之前；启动中/后端故障页也保留可拖动标题栏。原应用菜单继续承载快捷键，通过窄 `menu:popup` IPC 从自绘菜单打开，Windows 原生第二行菜单隐藏。
- Versions 明确拆成“本地版本控制（默认）”和“远程协作（显式启用）”：新论文项目的 `.git` 只在项目目录本地 commit/branch/diff/restore，不联网、不修改源码根、不会自动 push；没有 remote 时同步按钮不启用，只显示折叠的 GitHub/GitLab 添加入口。
- 新增 DELETE remote：用户显式添加 URL 后才可 Fetch、ff-only Pull、非强推 Push；移除 remote 只删连接配置，本地 commit、branch、工作文件和 HEAD 保持不变。后端模块/API/Electron 都有回归，仍不自动 merge 或 force push。
- 该发行阶段验证：316 collected；离线 `313 passed, 3 deselected in 22.50s`；TypeScript、Vite 92 modules、Electron `1 passed`、package 和 installer 全通过。显式 live 为 `315 passed, 1 failed`，唯一失败为 arXiv 公共服务 timeout/rate-limit。此证据已被本日顶部源码验证更新，但仍是旧安装器的发行证据。
- 该发行阶段 OpenAPI 为 134 paths/154 operations。安装器 135,381,021 bytes，SHA-256 `2A4C36AA4FFBF59316FF9DA91016C330D68BF8E7E500B3CE8062DF7E7B931E43`；冻结后端 `--check` 为 `no problems found`。本轮源码已增至 157/181 且暂未重打安装包，不能把新增功能算入该旧产物。

### 不可变正文证据、Rubric v3、盲评/金集导出与当前发行物

- 根因修复：Rubric v2 只绑定自动报告，而报告未保存正文内容/hash；项目正文后来修改后，无法证明评审者看过哪一版 prose。`quality.py` 现生成 schema v2 report 与可重放 `review_manuscript`：完整 primary/paired 文本、逐节 SHA-256/字符数、modified 标志、post-agent snapshot id、整体 fingerprint；被引来源也冻结摘要与 hash。
- Rubric 升至 v3。accepted 除原门禁外必须重算全稿、逐节与报告映射完整性，并提交 UI 实际阅读的 manuscript fingerprint。缺失、篡改、legacy-unbound 或 stale form 分别以稳定 blocker/409 拒绝；旧 v1/v2 记录不重写、不伪升级。
- 新增 `agents/review.py` 和 review packet GET/export：blind 包隐藏 run/project/model/pipeline/reviewer/既有决定、本地 PDF path 与 prompt/output；analysis 包恢复 provenance、模型/角色、tokens/cost 和评审。JSON 带 packet fingerprint，原子写入 `.papercreator/projects/<slug>/exports/reviews/`。
- evaluation summary 升至 schema v2：latest/history 语义不变，新增 distinct identified reviewer 的 review pair count、decision exact/kappa，以及总体/逐维 MAD、within-one、quadratic weighted kappa；同一 reviewer 重复提交不算独立复评。
- 桌面 Agents 增加冻结正文/配对文本/哈希阅读、全屏 blind mode、blind/analysis export、review mode/fingerprint history 和一致性面板。Electron E2E 以 blind+identified 两位 reviewer 完成同 Run 复评并验证重启。
- 该 v3 评审阶段当时验证：315 collected；离线 `312 passed, 3 deselected in 20.49s`，显式 live `315 passed in 38.28s`；TypeScript、Vite 91 modules、Electron `1 passed`（约 1.0m）、package、installer install→packaged app→data→upgrade→restore→uninstall preserve 全通过。当前证据已由上方标题栏/Git 阶段替代。
- Electron 发布可靠性收口：`PC_E2E=1` 时使用软件/进程内 GPU 路径，隔离当前 Windows Agent 会话中 Chromium GPU 子进程的 `0xC0000135`，正式安装版仍保留硬件加速；后端重启按具体 child 实例确认所有权，旧进程的异步 exit 回调不再污染新进程状态；停止后端改用应用持有的子进程句柄并等待端口释放，不再依赖标准用户可能无权执行的 `taskkill /T`。
- 品牌资源生成使用独立临时 userData/session/cache 与软件渲染，不再依赖全局 AppData/GPU 状态；父进程在 Chromium 完全退出后再次清理该临时根，避免 Windows 锁句柄留下构建残留。`brand:build` 与后续打包链已恢复可复现。上述软件 GPU 开关只用于 E2E/资产渲染隔离，不是生产默认配置。
- 该阶段安装器 135,038,721 bytes，SHA-256 `0AFAB72A8DF1DED6378E7ECE41996564C3443A808F01072AE4A54AEEA657349B`；608-file frozen backend 153,893,687 bytes；OpenAPI 134/153。均已被上方当前构建替代；review packet GET/export 合同仍保留。
- 仍未完成：真实专家随机化双盲金集、真实模型/费用矩阵和自动 gate 校准。当前 E2E 的一致性结果只是数学/交互 fixture，不是论文质量结论。

### Agent 论文质量门禁、Rubric v2 与当前发行物

- `agents/prompts.py` 改为复用 `writing.citations.CitationKeyMap`；`load_blackboard()` 先以项目全部 Papers 建 citation registry，再应用本轮子集，修复同姓同年碰撞在 prompt/export 不一致及 section run 改键。`orchestrator._persist()` 以 Reviser/Polisher 后最终正文 marker 为权威，重新生成 `cited_paper_ids`。
- 新增 `agents/quality.py`：每个 Agent 终态写 schema v1 `quality_report`，严格区分 citation key/metadata、文献使用、摘要证据、目标长度、双语配对等确定性检查与 CitationAgent/Critic 模型辅助证据；报告始终声明 `human_review_required=true`、`semantic_grounding_verified=false`，构建报告失败不会损坏已完成手稿。
- `agents/quality.py` 新增 Rubric v2 review requirements、逐篇 citation evidence 与 `build_review_target()`；每条评审固化报告版本/时间、Run status、automatic gate、required section/paper ids，并对排除动态 acceptance 投影的证据计算 SHA-256 fingerprint。后续评审不会改变前次所指证据，旧 v1 历史不迁移。
- 加固 `POST /api/agents/runs/{run_id}/evaluations`：accepted 只允许 done + quality report + gate pass/warn + 至少一个 modified section + 全部修改章节/被引论文逐项核对 + source confirmation + warn acknowledgement + 六维均 ≥3 + reviewer + 至少 20 字证据 notes；failed/cancelled、fail/unavailable、未知目标均不能绕过。revision/rejected 仍可追加到终态 Run。
- 新增 `GET /api/agents/evaluations/summary`：decision/维度/pipeline/model 按每 Run 最新评审统计，record/multi-review/disagreement/score spread 保留 append-only 历史。桌面增加逐节/逐篇 checkbox、URL/PDF 打开、blocker 列表、fingerprint/history 和评审摘要；无 LLM 时只禁用新 Run，历史/质量报告/评审仍可见。
- 新增 `test_agent_quality.py` 与 API/Electron 合同，覆盖共享 citation collision、项目全集子集稳定、最终正文元数据、结构 fail/模型 warning 分离、fingerprint 稳定、failed/fail 不可 accepted、未知目标、v2 append-only/汇总，以及桌面核对、warn、accepted、摘要和无模型重启恢复。E2E fixture 的无效 `[TEST2026]` 曾被门禁正确拒绝，现改为真实导入的 `[RESEARCHER2012]`，没有放宽产品检查。
- 无模型 E2E 首次失败定位为测试隔离问题：delete OpenAI env 后，非覆盖式 `.env` loader 会补回开发机配置。fixture 现为所有受支持 key/Ollama URL 保留空环境值，不读取或输出真实密钥，稳定证明第二次启动 0 LLM 时仍可审计历史。
- 该 v2 阶段验证为 307 collected / 304 offline / 307 live、OpenAPI 132/151、Electron 约 1.0 分钟；已被本文件上方 v3 阶段的 315 项和 134/153 证据替代。
- 该 v2 阶段安装器哈希为 `EDF287...`；已被上方 v3 当前安装器替代。自动 gate、模型辅助证据和人工 accepted 都不代表事实/方法被绝对认证。
- Wiki 复核已固化为 `npm run validate:docs`：61 个 Markdown、66 个本地链接、0 断链、0 空文档、0 未闭合代码围栏、0 UTF-8 replacement 字符、0 密钥特征命中。发布收口后没有 PaperCreator/Electron/backend/工作区 Python 进程、`papercreator-e2e-*`/`papercreator-installer-e2e-*`/品牌渲染临时根或 HKCU PaperCreator 卸载注册残留；OpenAPI 临时工作台已清除，未读取真实 `.papercreator/`。

### 安全大目录资源导入闭环

- `store/resources.py` 将 `code_project`、`dataset`、`supplementary`、`inbox` 目录导入改为确定性 inventory 驱动：预扫描文件/空目录、统计字节，检查目标卷空间和安全余量；根 symlink/junction/reparse point 拒绝，嵌套 link/reparse 与特殊文件不跟随并写 audit。代码导入排除常见依赖/构建目录和 `.env*`，保留 `.env.example`。
- 每个普通文件在复制前后校验 size、mtime、device、inode，且 resolved path 必须仍在源根；空目录与目录节点提交前再校验。源发生 TOCTOU 变化返回 `resource_import_source_changed`，空间不足返回 `resource_import_insufficient_space`。
- 托管副本写入同分类严格 `.partial-res_<16hex>`，以 4 MiB 分块复制并同步计算 SHA-256；完成后同盘原子 rename，最后才登记 `workbench_resources` ready row。DB 登记失败撤销最终副本；取消/失败清 staging，清理失败返回可操作的 `resource_import_cleanup_failed`。
- `POST /api/workbench/resources/import` 返回 HTTP 202 + `resource_import` Job，支持 progress/SSE 和 cooperative cancellation；普通文件/Idea 继续使用同步资源端点。`api/app.py` 启动时只回收严格 reserved stale staging，不触碰相似用户目录，回收失败不阻塞主服务。
- `WorkbenchPanel.tsx` 增加进度/阶段/Job id、取消和转入后台；Output → Jobs 可继续观察/取消。`events.waitForJob()` 先订阅 SSE，再读取 durable Job，并以每秒轮询兜底，消除任务在订阅前完成或 Renderer 重连丢事件导致的永久等待；资源完成事件刷新工作台、Library 和当前文档。
- `TestSafeDirectoryImports` 9 项覆盖成功/audit、直接取消清理、空间不足、文件与空目录源变化、嵌套 link 分类、stale cleanup、文件端点拒绝与 API 取消；工作台专项 17 passed。Electron 通过受限原生目录 fixture 导入 65 个代码文件并验证 `.env`/`node_modules` 排除、`.env.example` 保留、Job done、audit 正确和 0 staging 残留。
- 该阶段证据当时为 300 collected / 297 offline / 300 live、130 paths / 149 operations；现已被上方质量门禁阶段的 307 项全量回归与新发行物替代。目录导入的原子提交、空目录、排除策略和 0 partial 残留合同仍包含在当前回归中。
- 当时安装器为 134,989,198 bytes、冻结后端为 153,858,057 bytes；均已被上方当前产物替代。真实 10GB、百万小文件、网络盘、长路径、低空间卷、普通权限 junction/reparse 和断电矩阵仍未验证。
- 项目 Wiki 已同步目录导入的 API、架构、数据/运行/启动/写入流程、安全、存储、桌面、故障排查、任务状态、测试和 Agent 约束；交叉检查为 61 个 Markdown、66 个本地链接、0 断链、0 空文档、0 未闭合围栏、0 密钥特征命中。当前机器还确认无 PaperCreator/backend 进程、测试临时根、卸载注册项或 bundled-check 残留。

### LLM/Agent 外部故障恢复闭环

- `core/errors.py` 建立统一 LLM failure contract，覆盖 unavailable、configuration/authentication、rate limit、timeout/network、HTTP、坏响应/坏流、空响应、输出截断、模型错误与意外错误；诊断统一携带 `outcome`、`error_code`、`retryable`、HTTP/retry-after、用户 `hint`、provider/model 与 partial output 统计。
- `llm/backends.py`、`llm/client.py` 严格验证 OpenAI `[DONE]`、Anthropic `message_stop`、Gemini finish reason 与 Ollama `done=true`；坏 SSE/NDJSON 或缺少 terminal event 不再被当成成功。首个 delta 后禁止自动重放，避免重复正文，流式 timeout 也不再被静默扩大。
- HTTP、stream、embedding、JSON retry 和意外异常都写入 durable `llm_usage`；JSON 解析失败改写同一 usage 行而不重复计费。token budget 读取持久 usage，因此失败调用与 JSON retry 也进入预算。
- `agents/base.py`、`orchestrator.py`、`store/runs.py` 与 `core/jobs.py` 让 Step、Run、Job、SSE 共享同一 diagnosis 与终态；断流正文只进入失败 step 的 `partial_output`，不会覆盖完整手稿。Run 保存可重跑 request、pre/post snapshots 和 retry/restore 元数据；取消同样保持三层状态一致。
- 桌面 `events.ts`、Store、Agents/Settings 视图保留 `job.failed` 结构化字段；失败后刷新运行、手稿与时间线，展示 provider/model、HTTP、hint、快照与 partial audit，并提供“重试相同运行”“比较/恢复快照”和配置错误直达模型设置。
- 新增 `test_llm_faults.py` 的 16 项确定性故障合同；Electron E2E 在正常四步写作后，让第二次 Writer 收到首个 SSE delta 即 EOF，验证 Step/Run/Job failed、partial output、双快照、重试/恢复入口、旧手稿不损坏，以及后端/Electron 重启后的诊断恢复。
- 该阶段当时的专项证据已被本日后续全量回归与发行物替代；LLM 的 16 项故障合同包含在当时的 307 项测试中，并已进入当前 360 项全量测试。当前数字、大小与哈希只以上方最新证据为准。产物尚未签名，确定性本机故障测试不代表真实模型质量、费用或 SLA 验收。

### 学术 Provider 故障矩阵与定向恢复

- `ProviderStats` 新增稳定 outcome、machine error code、retryable、HTTP status、retry-after 和用户 hint；共享 HTTP client 将 429、5xx、401/403、其他 4xx 与非 JSON 响应写入结构化 details，`Provider.safe_search()` 再统一覆盖 unavailable、timeout、network 和 parser exception。
- `retrieval/pipeline.py` 不再让“全部 Provider 失败”在历史持久化前提前返回：部分失败保留成功论文和双侧 stats，全失败/零可用源仍记录实际 request、发布唯一 `search.done`。`search.provider` SSE 同步新增完整诊断字段。
- `SearchView.tsx` 增加 Provider diagnostics、失败来源历史计数和中英建议；“仅重试可恢复来源”从 `SearchResponse.request` 恢复全部条件，只选择 retryable 失败源并强制 `use_cache=false`，原失败执行继续保留。
- OpenAlex 新增 `retrieval.openalex_endpoint` / `PC_OPENALEX_ENDPOINT`，search/resolve/citation 共用；只接受远端 HTTPS，HTTP 限于 loopback。该能力用于受控镜像/代理，也让 Electron E2E 可走真实 HTTP 边界而不加入测试专用 Provider。
- `test_retrieval.py` 新增 safe-search 分类、MockTransport 429/503/401/坏 JSON、全失败历史、部分成功与终态事件合同；`test_core.py` 验证 endpoint 安全规则。Electron E2E 以 Local+本机 OpenAlex-compatible 服务验证 4 次 429 后的部分成功、诊断、单源恢复、三条历史和重启。
- 该阶段的专项合同和桌面恢复场景仍保留；后续 LLM 故障闭环增加了测试，当前数字与发行物以本日顶部最终证据为准。

### Windows 品牌与可复现打包

- 新增 `apps/desktop/assets/brand/icon.svg`：折页论文中的节点化 “P” 同时表达 paper 与多 Agent；无文字，适合小尺寸。`render-brand-assets.cjs` 用 Electron 从同一母版确定性生成 1024×1024 alpha PNG，并在宿主设置 `ELECTRON_RUN_AS_NODE=1` 时启动清洁 Electron 子进程。
- `main.cjs` 为开发窗口设置品牌 PNG；desktop package 将该 PNG纳入 ASAR 并声明 Windows icon。`after-pack.cjs` 使用锁定的 `rcedit@4.0.1` 写入应用 EXE 的 ICO、ProductName、FileDescription、CompanyName 和版本资源。
- 首次直接启用 electron-builder executable editing 时发现：普通 Windows 权限无法解压 `winCodeSign` 包内无关的 macOS 符号链接。最终方案恢复 `signAndEditExecutable=false`，改由项目钩子编辑资源；在全新 `ELECTRON_BUILDER_CACHE` 下只下载 NSIS、未下载 winCodeSign，构建成功，不要求管理员权限或 Developer Mode。
- 从最终 `PaperCreator.exe` 与 NSIS 安装器提取的 32×32 图标 SHA-256 均为 `78B3A3B9E86841CDECFAEC51E85F1DDFB34738637F8CE8BE1C81477B7E80E6F9`；ProductName=`PaperCreator`、FileDescription=`PaperCreator desktop workbench (Electron + React)`、FileVersion=`0.1.0`。品牌接入后 Electron E2E 仍为 `1 passed`（测试体 44.0s、总计 44.4s）。

### Windows 安装器自动化 E2E

- 新增 `scripts/installer-smoke.mjs`、根命令 `test:installer` 和 `test:release`。脚本发现 HKCU 已有 PaperCreator 卸载注册时拒绝运行，避免覆盖真实用户安装；所有测试状态仅进入经过前缀/父目录校验的系统临时根。
- 在安装目录与工作台均含中文/空格的条件下，静默安装最终 NSIS，由 Playwright 启动真实已安装 `PaperCreator.exe`，确认 `isDev=false`、bundled backend、Electron/FastAPI 指向同一个 `.papercreator`，并确认七类 library 目录。
- 通过真实 API 新建 Idea 和论文项目后，同路径静默覆盖安装并重启，Idea、项目、SQLite 与 marker 全部恢复；静默卸载后应用 EXE/HKCU 注册移除，而 `.papercreator`、数据库、Idea 和项目继续存在。测试结束无 PaperCreator/Electron/backend 进程、安装注册或 `papercreator-installer-e2e-*` 临时根残留。
- 首次运行捕获了 NSIS 卸载启动器先返回、临时子进程稍后删除注册项的时序；把即时断言改为 30 秒有界轮询后完整链通过。这是测试可靠性修复，不改变产品数据或安装行为。

### Remote Git 桌面安全闭环

- `vcs/git.py::remote_info()` 不再解析按空白分隔的 `git remote -v`，改为按 remote 名逐个调用 `git remote get-url`；修复中文/空格本地 remote 路径被截断。
- Git command/stdout/stderr、`set_remote` 与 remote list 对 `https://user:password@host` 形式统一脱敏；原始 URL 只传给 Git，不从 API/诊断回显。SSH 用户名和普通本地路径保持可读。
- `vcs/git.py` 新增 remote sync 状态、Fetch 和 fast-forward：Fetch 只更新 tracking refs；Pull 只接受当前分支、干净树和 `ahead=0, behind>0`，脏树、local-ahead、远端无分支、detached HEAD 或分叉均拒绝，不启动自动 merge。
- `versions.py` 新增 `/git/fetch` 与 `/git/pull`。真正快进前执行 DB→disk 同步预检、DB snapshot 和磁盘手稿备份，`merge --ff-only` 后 reindex DB；因此 UI 不会继续显示 pull 前的数据库镜像。
- `VersionsView.tsx`、API types/endpoints 增加 remote 配置、脱敏地址、ahead/behind/diverged 状态、Fetch、Pull (ff-only) 和非强推 Push，并解释 Credential Manager/SSH 与外部客户端分叉解决边界。
- `backend/tests/test_git_remote.py` 扩展为 5 个 bare remote 合同；`test_api.py` 增加 pull recovery/reindex 合同。Electron E2E 用中文/空格 bare remote 和真实协作者 clone 验证 UI 配置→Push→协作者 commit→Fetch 不改文件→Pull recovery/reindex→再次 Push。
- Electron E2E 进一步从同一 remote 基线制造协作者/本地各 1 个提交，验证 UI 显示 ↑1/↓1/diverged、Pull 返回受控 409、本地独有文件保留且远端独有文件不进入工作树。等待 Push 按钮 disabled→enabled，避免复用旧 toast 误判完成；预期冲突窗口只消费精确一条 409，其余 console error 仍失败。
- 该阶段的本地 bare remote 合同和 Electron 场景仍有效；后续 LLM 故障闭环已重新跑全量回归、打包与 installer E2E，当前数字、大小和哈希以本日顶部最终证据为准。
- 仍未验收真实 GitHub/GitLab、SSH/Credential Manager 和人工分叉解决；产品当前刻意不自动 merge 或 force push。

### 六类桌面导出与 LaTeX 引用修复

- Electron E2E 从 Markdown/DOCX 扩展到 LaTeX project、cited-only BibTeX、bundle ZIP 和 Overleaf ZIP；逐项读取磁盘，核对正文/临时修订隔离、文档类、sections、`\cite{researcher2012}`、references.bib、独立 BibTeX 和 ZIP 中央目录，并验证重启历史。
- E2E 暴露确定性 Agent fixture 使用了错误大小写引用键；改为真实 `RESEARCHER2012`，使 cited-only bibliography 验证有意义。
- E2E 随后发现产品缺陷：`to_latex_citations()` 先生成 `\cite`，`markdown_to_latex()` 又把反斜杠按普通文本转义。`latex_project.py` 现用私有占位符保护生成命令，Markdown 转换后恢复；`test_convert.py` 增加 main/section 中真实 cite 与不存在 `\textbackslash{}cite` 的回归。
- 专项 `37 passed`、当时完整后端与 Electron 回归通过；该阶段安装包已被本日后续 Remote Git 桌面闭环构建替代，当前产物证据见上方对应小节。

### 自动检索桌面闭环

- `SearchRequest` 新增持久化的 `use_llm_expansion`；`SearchBody.to_request()` 不再排除该字段，pipeline 的异步/同步入口默认读取请求值。修复此前“预览显示规则展开，但正式后台搜索仍默认调用 LLM”的跨层合同错误。
- 历史 rerun 现在保留原始 mode、seed、providers、filters 和 `use_llm_expansion`，只按接口参数覆盖 `use_cache`；增加 `test_background_search_preserves_llm_expansion_choice` 与 `test_history_rerun_preserves_expansion_choice`。
- `SearchView.tsx` 进入页面即刷新 Provider 可用性，使 `.bib` 导入后 Local Provider 无需重启即可使用；Provider 默认选择每个页面生命周期只初始化一次，用户可清空网络源后只选 local。
- 已有论文模式新增当前项目文献库选择器，选择后自动将标题与摘要填入仍可编辑的 seed；新增 Search history 表，显示 mode、seed/query、Provider、结果数、时间与规则展开标记，“重新检索”创建新记录而不覆盖旧记录。
- Electron E2E 在 12 篇托管 BibTeX 上分别执行 Idea 与已有论文的真实后台 Local 搜索；两条历史结果非零、保存 `use_llm_expansion=false`，mock LLM 调用为 0，重复 upsert 后仍为 12 篇，整套 Electron 重启后两条历史恢复。品牌接入后的最新运行仍为 `1 passed`，测试体 44.0s、总计 44.4s。

### 手稿同步安全

- `store/documents.py`：新增 DB 渲染镜像/磁盘托管文件的独立 SHA-256 摘要、项目 `.papercreator/manuscript-sync.json` 原子基线、8 类状态、`sync_status()`/`ensure_sync_safe()`；普通 flush/reindex 不再覆盖已变化的目标侧，冲突返回 `manuscript_sync_conflict`。
- 强制选边会先把即将被覆盖的 DB 或磁盘镜像写入项目 `.papercreator/conflicts/`，包含 `conflict.json` 和当时同步证据；备份名增加随机 id，避免同秒冲突。
- `api/routes/writing.py`：增加 `GET /api/writing/{project_id}/sync-status`；flush/reindex 增加 `force`，文件覆盖 DB 前另建 snapshot。
- `EditorView.tsx` 与 API 类型/端点：每 5 秒读取同步状态；显示冲突类型，提供“以文件为准”“以数据库为准”“查看文件”，强制操作明确确认并展示恢复路径。

### 版本与外部写入保护

- Git checkout、Git restore、snapshot restore、Agent `_persist`、Overleaf apply 均在第一次破坏性修改前检查对应同步方向。
- Git discard 改为：预检 → DB snapshot → 磁盘手稿副本 → `git diff --binary HEAD` 恢复 patch → 还原 tracked 文件（untracked 保留）→ reindex DB。patch 由 Git 直接写盘，避免敏感 diff 进入日志/响应。
- `VersionsView.tsx` 增加完整 staged/unstaged/untracked 范围、明确危险确认、discard 成功后的 document/timeline 刷新，以及探索分支创建/checkout 控件；成功 toast 返回恢复 patch 与手稿备份路径。
- 修复三个由真实 Git E2E 捕获的跨层问题：`loadTimeline()` 现在并行读取完整 `/git/status`，不再把 timeline 摘要冒充完整状态；status 使用 `--untracked-files=all` 精确列出 nested 文件；手动 init 成功后持久化 `git_enabled=true`，不再出现“仓库已建但 Save version 只建快照”的半初始化状态。
- Electron E2E 验证禁用 Git 的项目经 UI 初始化后产生同名 commit+snapshot；tracked 手稿和 untracked 笔记混合时，discard confirm 展示准确范围，磁盘存在有效 binary patch/手稿副本，tracked 回到基线、untracked 保留且 DB `in_sync`；随后探索分支 commit 并 main↔分支↔main，正文和分支独有文件都随 checkout 正确重载。
- 扩展 Electron E2E 主动改写编号章节文件，验证普通 UI 保存精确产生一次受控 HTTP 409、sync state 进入 `diverged`，再分别确认“以数据库为准”会在项目 `.papercreator/conflicts/` 保存原磁盘文件，“以文件为准”会先建立 `before resolving manuscript conflict from files` 数据库快照；两个方向都回到 `in_sync` 并继续通过版本、导出和重启恢复。
- E2E 捕获同步解决后的 dirty 误状态：数据库优先现在只清除与重载 DB 完全相同的 dirty 值，保留更晚输入；`CodeEditor` 使用 external transaction annotation 更新外部正文，不再触发用户 `onChange` 或进入 undo history。数据库/磁盘实际上已保存时，Save 按钮会正确恢复禁用。

### Agent 审计、增量写入与桌面恢复

- `store/runs.py` 增加 `append_step_prompt()`；`agents/base.py` 在 step 生命周期绑定 `_active_step_id`，`ask()`/`ask_streaming()` 调用前原子追加完整 SYSTEM/USER prompt。同一步并发精读多篇时以 `===== NEXT LLM CALL =====` 分隔，不再被最后一次调用覆盖；Writer 的“审查”弹窗可看到真实 prompt/output。
- Blackboard 增加 `modified_section_keys`；Writer/Reviser/Translator/Polisher 修改时登记，orchestrator `_persist()` 仅按 outline 顺序写这些章节。已有摘要仍可作上下文，但不会被重写或误计入 `sections_written`。
- `state/store.ts` 在 Agent job 完成后重新 `loadRuns()`，run detail 与 history 行不再出现 done/running 不一致；Run history 表增加可访问名称，E2E 能精确定位而不误匹配角色表。
- Electron E2E 增加本地确定性 OpenAI-compatible `/v1/chat/completions` 服务：Reader/Synthesiser 返回严格 JSON，Writer/Translator 走真实 SSE；验证 Authorization、4 次请求、Skill 注入、token、prompt/output audit、英中双语文件、pre/post snapshot 和 Electron 重启后的 done/4-step run。它证明系统集成，不代表真实云/本地模型论文质量。
- E2E 重载检查改为等待标题栏 `Close project`，明确区分“项目列表已加载”和“最近项目已重新打开”，消除启动竞态而不增加任意 sleep。

### API、测试与发行

- 该阶段 OpenAPI 为 12 组、130 个唯一路径、149 个 HTTP 操作；随后质量评审阶段增至 132/151，当前值见本日顶部记录。
- 增加 DB/disk 分叉、旧基线、强制备份、强制文件导入 snapshot、API 409、checkout、discard、手动 Git init 持久启用/nested untracked、snapshot restore、Agent 和 Overleaf 高风险链测试，以及 Search 后台/历史展开开关、prompt append/dirty-section 专项；当前完整基线见上方“Remote Git 桌面安全闭环”。
- 扩展 Playwright Electron 真实 E2E：以 12 篇三主题本地 BibTeX 通过 Library 入口建立托管副本，验证项目计数刷新、Idea/已有论文 Local 检索、规则展开/历史/重启、Hashing+PCA Three.js 12 点图谱、Idea 精确投影/近邻/seed、移除和重加（12→13→12→13）；再串联 CodeMirror/编号文件、4 步 Agent/Skill/双语、同步冲突双向恢复、Git init/commit/discard recovery/branch checkout、快照 diff/原生 confirm 回滚、Markdown/DOCX UI 导出、后端/页面/Electron 重启，并确认重启后两条搜索历史、Library 13 篇、图谱 13 点及 seed、正文/Agent run/冲突与 Git 快照/commit+snapshot/导出历史恢复；品牌接入后 `1 passed`（测试体 44.0s、总计 44.4s），无工作区进程或系统 `papercreator-e2e-*` 残留。
- `electron/main.cjs` 在 `PC_E2E=1` 时抑制 Explorer/外链打开；新增 `PC_E2E_OPEN_FILE` 仅接受隔离工作台内 fixture，以穿过真实 Library 文件导入入口。产品 API、数据库、writer 和项目文件写入仍是真实链路；只有外部 LLM 供应商由本地 HTTP fixture 代替，正常开发/安装行为不变。
- E2E 发现并修复 `store.ts::saveSection()` 保存竞态：服务端章节与 dirty 清理改为原子状态更新，仅在请求期间没有新输入时清除 dirty，避免失焦后 CodeMirror 回灌旧值。
- E2E 发现并修复 Library 导入后项目 `stats.papers_in_project` 不刷新：`LibraryView`/`WorkbenchPanel` 导入完成后重新加载 document，用户无需重开项目即可构建图谱。
- `analysis/embeddings.py`、`analysis/incremental.py`：固定 MD5 bucket 的 Hashing 不依赖 corpus，现进入 portable cache/embedding 路径并支持完全离线的 Hashing+PCA 精确增量定位；只有 TF-IDF 保持 corpus-relative。新增同文本独立/批量向量一致和旧点不移动的回归测试。
- `PositionResult.method`、前端类型/Landscape UI：明确区分 `exact_transform`（精确投影）和 `interpolated`（邻居插值）；Landscape 选项改为“可增量（Hashing + PCA）”。
- 修复 `/api/analysis/capabilities` 的残留元数据，使 Hashing 报告 `portable=true`、TF-IDF 报告 `false`，并以 API 测试和打包后端真实 HTTP 请求验证。
- `place-idea`/`place-paper` 直接声明 `response_model=PositionResult`，使 Swagger/OpenAPI 包含完整响应与 `exact_transform|interpolated` 枚举；新增合同测试。
- `npm run typecheck`、`npm run build` 通过；Vite 91 modules transformed。
- 当前发布产物以本日顶部“安全大目录资源导入闭环”记录为准；此前 LLM、六类导出与 Remote Git 阶段的中间安装包已被替代。
- 最终冻结检查使用的 `test-results/bundled-agent-check-20260728-0316/` 已在确认绝对路径与测试边界后删除；没有 PaperCreator/Electron/backend 进程、系统 installer/E2E 临时目录或卸载注册项残留。

### 尚未完成

- 该阶段同步冲突按整篇文档保守判断；当前已由顶部记录的 schema v2 非重叠章节安全合并取代，同节双改仍保守阻塞。
- Electron E2E 已覆盖确定性 Local+OpenAlex 429 故障/恢复、Remote Git UI 和六类无外部依赖导出，后端合同覆盖更多结构化 Provider 故障及 Git 安全拒绝；尚未覆盖逐个公网源的长期 live 漂移、真实模型 Agent 质量与故障、真实远端认证/人工分叉解决、旧项目无基线冲突、PDF/真实 Overleaf Git 和首次原生工作台 dialog；clean Windows VM 与代码签名仍是发行 P1。

## 2026-07-27

### 工作台与分类资源

- `core/paths.py`：确立产品名 PaperCreator 和 `<用户所选文件夹>/.papercreator/` 单根合同；项目改为 `projects/`，增加 7 类 library、workbench manifest、整体移动/备份语义。
- `core/db.py`、`core/models.py`、`store/resources.py`：数据库升至 schema v2，新增相对路径的 `workbench_resources`、托管复制、摘要、来源和项目/论文关联。
- `api/routes/workbench.py`：新增 5 个工作台 API；旧 `/api/library/import` 也改为先复制托管副本再解析。
- `WorkbenchPanel.tsx`、Store/types/endpoints 与导航：实现“新论文”和 Idea、参考论文、我的论文、项目代码、数据集、补充材料、待分类的分离入口；最近项目从 localStorage 移入工作台 SQLite。

### Windows 自包含发行

- `scripts/backend_entry.py`、`build-backend.mjs`、`backend/pyproject.toml`：增加 PyInstaller onedir 后端构建。
- Electron 安装态只启动 bundled backend；冻结态不读取源码仓库 `.env`；浏览器数据写入当前 `.papercreator/electron/`，生命周期日志写 `logs/desktop.log`。
- electron-builder 首次输出无品牌资源的 `PaperCreator-Setup-0.1.0.exe`；Renderer 依赖只打一次，ASAR 约 6.3 MB。该旧产物已被上方 2026-07-28 品牌版本替代，当前大小与哈希以“Windows 品牌与可复现打包”为准。
- 已真实验证静默安装、启动、Idea/项目持久化、同路径覆盖安装、重启恢复、卸载及卸载后工作台数据保留。尚未在 clean VM、签名环境验证。

### 修复

- `scripts/setup.mjs`、`run-tests.mjs`、`run-backend.mjs`：Windows 子进程改为不经 shell 传参；修复 Python 3.11 被误报缺失、pytest marker `not live` 被拆词及安全弃用警告。setup 已真实复验。
- `api/routes/system.py`：直接导入 `uptime_seconds`，避免 `papercreator.api.app` 模块名被 FastAPI `app` 实例遮蔽导致 health 500。
- `api/routes/export.py`：把 `/convert` 静态路由移到 `/{project_id}` 前，避免被解析为 project id。
- `core/jobs.py`：线程池 shutdown 后可按需重建，支持 TestClient/lifespan/reloader 在同一解释器重新运行任务。

### 配置

- `.env.example` 补充 LLM base URL overrides、`PC_OLLAMA_MODEL`、`PC_HF_ENDPOINT`、`PC_OFFLINE_MODELS` 和桌面 `PC_PYTHON` 说明；未写入真实密钥。

### 测试

- 修复前：224 个离线选择中 7 失败（3 根业务原因 + runner 参数问题）。
- 增加工作台测试后：`232 passed, 3 deselected`（235 collected）；3 个 live 网络测试未运行。
- `npm run build`：TypeScript + Vite 生产构建通过。
- `npm run setup -- --no-analysis --backend-only`：安装与 `--check` 成功。

### 文档

- 从只有一个且链接大量不存在文件的 `docs/README.md`，重建为完整项目级 Wiki；所有状态基于当前扫描/运行，不含实例级 Wiki。
- 增加根 `README.md`，提供真实状态、快速启动和 Wiki 入口。

### 尚未完成

- clean Windows VM、代码签名、installer 自动化、真实 LLM/live API 质量验收。
- 超大代码/数据目录导入 Job 化与取消/空间检查已于 2026-07-28 完成；尚缺真实规模和 Windows 文件系统矩阵。
- 根部误生成 `main.cjs` 已删除；根 `dist/` 的递归删除被执行策略拒绝，已记录为待手工清理，不影响正式 package 路径。
- 根 Git 初始化与历史提交（需用户确认仓库策略）。

## 日期待确认（本轮之前已有代码）

### 新增

- Electron/React 工作台、FastAPI、SQLite 初始 schema v1（本轮已迁移至 v2）。
- 9 个检索 Provider、分析全链路、11 Agent/4 pipelines、4 built-in Skills。
- 双语写作、Markdown/LaTeX/DOCX/BibTeX/Overleaf、Git+snapshot 版本。
- 初始 227 项测试及 smoke 脚本（2026-07-27 增至 235 collected；当前数见上方 2026-07-28 记录）。

无法从 Git 证明具体日期、提交、作者、修改前行为或是否部署。

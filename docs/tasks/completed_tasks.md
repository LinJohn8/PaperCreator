> 文档用途：已完成且有当前证据的任务  
> 最后检查：2026-07-28

# 已完成任务

## 首启快速开始与终端用户恢复入口

- 完成内容：空工作台首启显示五步任务清单，进度由工作台分类、项目、文献、手稿字数和当前项目时间线推导；每步直接打开真实下一动作。用户可本次稍后关闭，或把版本化偏好保存到工作台设置；Help 与命令面板始终可重开，LLM/导出为可选增强而非强制完成条件。
- 入口修复：原生“新建项目”现在直接打开同一创建表单；Help 日志入口和后端故障页调用受限 `app:openLogs` IPC；安装态不再显示源码/pip 指令。新建表单补齐 dialog 语义、label/id、关闭名称、Escape、忙碌保护和 Tab 循环。
- 验证：设置默认/patch/health round-trip；TypeScript 与 95-module build；真实 Electron 首启、Help、命令面板、菜单新建、日志 IPC、关闭偏好和第二次启动不自动出现，并保存 1365×900 与 1100×700 截图。全量为 360 collected / `357 passed, 3 deselected`，Electron `1 passed (1.6m)`。

## 配置优先级与来源可观测闭环

- 完成内容：配置合同统一为 default < UI settings < secret file < `.env` < process environment；环境层改为只包含实际存在的变量，避免缺省环境值覆盖已保存设置；普通空字符串可真正清除旧值；reload 会刷新未被进程改写的 `.env` 注入值。
- 安全与诊断：新增 `GET /api/settings/sources`，只报告优先级、文件路径/存在性、字段名、环境变量名和 override 字段，不返回任何配置值或密钥；Settings 通用页显示精确分离的 `.env` 与 process environment 控制字段。
- 验证：新增持久设置/稀疏环境/环境强制/空值清除/来源不泄密/API 合同测试；当前 360 collected、357 项非联网全量通过，TypeScript/95-module build 通过。

## 单语言启动恢复、工作台项目体验与设置修复

- 完成内容：启动恢复最后工作台、最后项目和语言；第二实例可打开检索或选择其他工作台；中文/英文单选显示并持久化；修复检索源空 settings PATCH 与最后一源保护；缩短普通通知；项目大卡和点击/创建后进入手稿；标题栏增加右侧 AI 入口。
- 涉及文件：`electron/main.cjs`、`TitleBar.tsx`、`ActivityBar.tsx`、`SettingsView.tsx`、`ProjectsView.tsx`、`WorkbenchPanel.tsx`、`state/store.ts`、`app.css`、E2E。
- 验证：TypeScript/95-module 生产构建通过；桌面长链 `1 passed (1.6m)`，验证快速开始、单语切换/重启、最后项目、项目卡重入、章节 CRUD/双目标、提示词变量、助手归档导入/脱敏、翻译 preview-first 应用、逐章节合并、仅本地 Git，以及未按保存直接退出后的正文恢复。
- 遗留：首次/第二实例原生对话框尚未形成独立自动化；少量后端诊断数据仍按原始英文展示。

## 多格式论文、手稿 CRUD、翻译和投稿模板

- 完成内容：PDF/DOCX/MD/TXT/TeX 提取及全文 sidecar；扫描 PDF 可选本地 Tesseract OCR，含渲染器/语言包探测、页限和超时；复杂 DOCX 有界 OOXML 提取保留表格行、常见公式线性文本、脚注/尾注与结构审计；手稿 preview→apply；章节 CRUD/双目标；MyMemory/LLM 翻译；11 个原创结构；用户授权 venue ZIP。
- 涉及文件：`importers/document_text.py`、`importers/local_ocr.py`、`writing/manuscript_import.py`、`writing/venue_templates.py`、Writing routes、`EditorView.tsx`、DB v3、可选 OCR extra 和测试。
- 验证：后端非联网全量当前 `357 passed, 3 deselected`；复杂 DOCX exact golden、OCR mock/缺失能力、术语、模板、导入 SHA-256/快照、ZIP 路径安全均通过；TypeScript/构建通过。
- 遗留：当前机器无真实 OCR 引擎/渲染器；真实扫描 corpus、复杂 Word/venue 人工编译验收和公共翻译 SLA 需专项环境。

## 项目 AI 助手与提示词模板

- 完成内容：项目感知只读聊天与二次确认动作；DB v4 提示词；DB v5 线程/消息；DB v6 归档来源映射。支持 JSON/JSON.GZ 受限保存/选择，preview→confirm 原子导入，来源 ID+完整内容 fingerprint 幂等，删除后恢复和来源变化副本；保留期范围批删与消息级不可逆敏感内容清理均有防并发 token，content/actions/meta 全清并只留 hash/大小/时间/原因审计。
- 涉及文件：Assistant/Prompt routes/stores、Electron preload/main、`AssistantPanel.tsx`、endpoints/types、DB v4-v6 和专项/Electron 测试。
- 验证：线程作用域/顺序、导入跨范围/幂等/变化副本、gzip 大小边界、脱敏不泄原文和项目级联合同通过；真实 Electron 验证压缩归档、导入与消息清理，无模型重启恢复。
- 遗留：独立短 Assistant/Prompt 场景；Agent Run prompt/output 是另一审计数据域，尚无联动清理策略；未发表上下文仍由用户工作台本地保管。

## 长文与批量翻译可靠性

- 完成内容：MyMemory 公共外发必须显式确认；同步短文≤10k，长文/批量 Job≤100k 字符/250 请求；稳定句段分块，代码围栏/展示公式/分隔空白不外发；限速、Retry-After/timeout/network 有界重试和协作取消。Job 只保存完整预览，完成结果可恢复；应用前验证每节源/对照 SHA-256 和磁盘同步，创建恢复快照后同事务一次写入，重复 apply 幂等。
- 验证：分块结构/429 重试、公共确认、preview-only、取消零写入、陈旧源拒绝、快照/原子应用/幂等后端合同通过；真实 Electron 验证完整预览前 `content_zh` 不变、确认后一次写入。
- 遗留：真实本地 OCR/复杂 Word 人工验收、公共服务低频 SLA；运行中进程重启不自动重发公共文本。

## 逐章节同步合并、CLI 与开发验收收口

- 完成内容：`manuscript-sync.json` 升至兼容读取 v1 的 schema v2，保存逐节 filename/DB/disk fingerprint；仅在两侧修改既有章节集合完全不重叠、无结构变化且 preview token 未陈旧时合并，写前保存 DB snapshot 和两侧镜像。同节双改、增删/重命名和 v1 基线继续阻塞。
- 工具与合同：CLI `--host/--port` 贯穿 app factory/lifespan/CORS/热重载；新增 157 paths/181 operations 的 method/path route snapshot；E2E 关闭超时精确回收 owned PID tree；`cleanup:e2e` 默认 dry-run、严格 Temp 前缀/年龄/JSON 输出。
- 验证：逐章节相关 53 项、CLI/助手相关 22 项、全量 360 collected/357 非联网通过；真实 Electron `1 passed (1.6m)` 验证安全合并、恢复材料、归档/脱敏、LaTeX 全文引用和退出清理。

## Windows 后端优雅退出与 SQLite 收口

- 完成内容：Electron 为 owned backend 生成不暴露给 Renderer 的随机 capability；关闭窗口先断开 Renderer SSE，再调用隐藏回环关机路由，等待 Uvicorn lifespan、WAL checkpoint 和 Python 退出。backend-only restart 有 2 秒连接 grace；Windows venv launcher 卡死才对精确 PID tree 强制兜底。
- 涉及文件：`electron/main.cjs`、`papercreator.__main__.py`、`api/routes/system.py`、`api/app.py`、`core/db.py`、API/Electron E2E。
- 验证：错误/缺失 capability 为 404，精确值只调用一次 callback；真实 Electron 四次启动，第三次写入 dirty 文本后直接退出、第四次恢复；日志包含 `Application shutdown complete`、checkpoint `busy=0`，desktop log 为退出码 0；全量后端与 E2E 通过。
- 遗留：Windows Defender/索引器可能在所有应用句柄关闭后继续占用系统临时测试 DB；E2E 仅对严格 temp/prefix 路径有界退避并告警，不影响或清理用户工作台。

## VS Code 式标题栏与本地 Git/可选远程分层

- 完成内容：Windows 以一行标题栏容纳品牌图标、File/Edit/View/Help、项目身份、命令/连接状态和原生窗口控件；隐藏重复系统菜单栏但保留快捷键/原生菜单模型。Versions 默认只做项目内本地 Git commit/branch/diff/restore，remote 折叠为显式可选层；新增 remote 删除，断开 GitHub/GitLab 类连接不会删除本地历史。
- 涉及文件：`electron/main.cjs`、`preload.cjs`、`TitleBar.tsx`、`app.css`、`VersionsView.tsx`、`api/endpoints.ts`、`vcs/git.py`、Versions routes、后端与 Electron 测试。
- 验证：316 collected；离线 `313 passed, 3 deselected`；TypeScript/Vite 92 modules；Electron `1 passed`（约 1.2m，含标题栏视觉取证）验证 logo 在 File 左侧、菜单桥/隐藏第二行、remote 显式启用、分叉拒绝、remove 后 HEAD 完全相同及重启全链；package、冻结 `--check` 和 installer E2E 通过；OpenAPI 134/154。
- 发布：安装器 135,381,021 bytes，SHA-256 `2A4C36AA4FFBF59316FF9DA91016C330D68BF8E7E500B3CE8062DF7E7B931E43`；608-file frozen backend 153,894,049 bytes。
- 遗留：真实 GitHub/GitLab/SSH/Credential Manager 认证与人工分叉解决仍需外部环境；本轮 live 的 arXiv timeout/rate-limit 是公共服务波动，不是确定性回归失败。

## 开发基线与一键安装修复

- 完成内容：修复 Windows Python 探测/子进程参数、健康 API、转换路由、JobManager 重启。
- 涉及文件：`scripts/*.mjs`、`api/routes/system.py`、`api/routes/export.py`、`core/jobs.py`。
- 验证：setup/diagnostics 成功；该阶段为 307 collected / 304 offline / 307 live；当前全量见本文件 v3 任务。
- 部署：本机开发与自包含安装链路验证；未正式发布。
- 遗留：clean VM 和代码签名仍未完成；正式图标已在本轮补齐。

## 单根工作台与分类导入

- 完成内容：PaperCreator 命名；首次/切换工作台；所有系统数据进入 `<folder>/.papercreator`；新论文与 Idea/参考论文/我的论文/代码/数据集/补充材料/待分类分离；导入复制托管副本。
- 涉及文件：`core/paths.py`、`core/db.py`、`core/models.py`、`store/resources.py`、workbench/library routes、WorkbenchPanel/Store/API types。
- 验证：工作台专项当前 17 passed；实际 Electron 中文页面；安装态创建 Idea/项目并在升级/重启后恢复。
- 遗留：工作台迁移向导、首页删除/重分类 UI；目录真实极限规模矩阵见下项遗留。

## 安全大目录资源导入闭环

- 完成内容：为 `code_project`、`dataset`、`supplementary`、`inbox` 目录新增 202 `resource_import` Job；实现确定性 inventory、文件/字节统计与空间安全余量，根 link/reparse 拒绝、嵌套 link/reparse/特殊文件不跟随并审计，代码依赖/构建目录和 `.env*` 排除（保留 `.env.example`），源 size/mtime/device/inode/resolve 与空目录 TOCTOU 复核，4 MiB 分块复制/摘要、同分类 `.partial-res_*` staging、同盘原子 rename、DB 最后登记和登记失败撤销；取消/失败清理 staging，启动只回收严格保留名。
- 涉及文件：`store/resources.py`、`api/routes/workbench.py`、`api/app.py`、`test_workbench.py`、桌面 WorkbenchPanel/events/endpoints/types/store、Electron main 与 E2E。
- 验证：`TestSafeDirectoryImports` 9 项覆盖 success/audit、直接取消清理、空间不足、文件与空目录源变化、嵌套 link 分类、stale cleanup、文件端点拒绝和 API 取消；工作台专项 17 passed。Electron 原生目录 fixture 实际导入 65 个代码文件，断言 `.env`/`node_modules` 未复制、`.env.example` 保留、audit/Job done 且 0 staging 残留。最终冻结后端 HTTP 复验 `resource_import=done`、`strategy=atomic_managed_copy`、空目录保留和 0 partial 残留。
- 部署：已进入当前冻结后端与 NSIS，并通过 package、installer 覆盖升级/卸载保留链。
- 遗留：真实 10GB、百万小文件、网络盘、超长路径、低空间卷、普通权限 junction/reparse、杀软占用和断电矩阵尚未基准；当前异常重启策略是清 staging 后重来，不支持断点续传。

## Windows 自包含安装链路

- 完成内容：PyInstaller onedir 后端、Electron packaged executable 查找、NSIS、ASAR 体积优化、冻结态 env 隔离、工作台内 Electron 数据和 desktop log。
- 涉及文件：`scripts/backend_entry.py`、`build-backend.mjs`、pyproject、root/desktop package、Electron main。
- 验证：bundled `--check`；8-paper TF-IDF/PCA/KMeans；`npm run test:installer` 自动执行安装→真实已安装 EXE/bundled backend→写 Idea/项目→覆盖安装→重启恢复→卸载→注册清除且数据仍在；安装/工作台路径含中文和空格。
- 遗留：clean Win10/11 VM、标准用户/杀软矩阵和代码签名。

## 免费多源检索主链

- 完成内容：9 Provider registry、query expand、限流/cache、并发、过滤、去重合并、RRF、history/resolve、idea/paper mode；桌面动态刷新 Provider、从项目文献库选论文、展示/重跑检索历史；`use_llm_expansion` 作为可复现请求贯穿后台 Job 和 rerun。
- 涉及文件：`core/models.py`、`retrieval/`、search/library/store routes、`SearchView.tsx`、桌面 Store/E2E。
- 验证：parser/dedupe/rank/registry/API tests；该阶段 live 307 passed；当前全量见 v3 任务。Electron E2E 继续覆盖 429 隔离/恢复和历史。
- 遗留：尚无 nightly 趋势/字段漂移监控和 Provider SLA；结构化失败分类已完成，但一次 live 成功不能保证未来公共服务状态。

## 研究图谱主链

- 完成内容：embedding fallback/cache、3D reducer、cluster、keyword/trend、heatmap、gap candidate、graph、incremental place/remove；纠正固定 Hashing 的 portable 语义，提供无需模型/网络的 Hashing+PCA 精确增量定位；PositionResult 可审计区分 exact transform 与邻居插值。
- 验证：analysis unit/full pipeline/incremental/graph tests 全通过；同一文本独立/批量 Hashing 向量一致；真实 Electron UI 以 12 篇三主题 BibTeX 建图，Idea 12→13、移除 13→12、重加 12→13，重启后 13 点/seed 恢复且旧点不移动。
- 遗留：真实大库性能、语义模型本轮环境、研究解释人工验收。

## 多 Agent/Skill/写作/版本/导出基线

- 完成内容：11 roles、4 pipelines、budget/cancel/snapshot、三级 Skill、双语章节、引用、5 模板、核心导出、Git+snapshot API/UI；Versions UI 含手动 init、双版本保存、完整 status、可恢复 discard、探索分支创建/checkout；Agent step 原子累计完整 prompt，Blackboard 仅持久化本轮 `modified_section_keys`，完成后刷新 run history。
- 验证：合同/转换/store/API tests、前端 build，以及本地 OpenAI-compatible HTTP 上的 Reader/Synthesiser JSON、Writer/Translator SSE、Skill、token、prompt/output audit、双语写盘和 Electron 重启恢复。
- 遗留：真实 Provider/模型论文质量、实际费用/长文矩阵、真实 Git 认证/分叉解决、Overleaf Git 和 venue 排版；确定性 E2E 不是质量验收。

## Agent 引用一致性、自动质量门禁与 Rubric v2

- 完成内容：prompt 与导出共用 `CitationKeyMap`；Blackboard 使用完整项目 citation registry，section/retry 子集不重排键；`_persist()` 从 Reviser/Polisher 后最终正文重建 `cited_paper_ids`。每个终态 Run 写入 schema v1 `quality_report`，确定性结构检查与 CitationAgent/Critic 模型证据分离；citation registry 固化逐篇来源入口。Rubric v2 逐节/逐篇核对并绑定报告时间/gate/required ids/SHA-256 evidence fingerprint；桌面展示 blockers、URL/PDF、历史和 latest-vs-append-only 摘要，无 LLM 时仍可审计。
- 涉及文件：`agents/quality.py`、`agents/prompts.py`、`agents/orchestrator.py`、`store/runs.py`、Agents API/types/endpoints/view、`test_agent_quality.py`、API 与 Electron E2E。
- 数据保证：人工评审只允许终态 Run，以 IMMEDIATE transaction 追加到既有 `agent_runs.result`；accepted 必须 done、gate pass/warn、完整逐节逐篇、来源/warn 确认、六维均 ≥3、reviewer 和 ≥20 字 notes；旧 v1 不重写且不能补写 v2 accepted。fingerprint 不受后续 acceptance 投影变化影响；无需 DB migration，重启可恢复。
- 验证：引用碰撞/子集稳定/最终正文权威/自动 fail 与 model warning 分离均有后端合同；Agent API 覆盖 fingerprint、failed/fail 拒绝、未知目标、v2 append-only、summary/disagreement、running 409；Electron 覆盖逐节逐篇、warn、accepted、摘要、保存和无模型后端/Electron 重启恢复。
- 遗留：自动门禁不能证明论断真实；真实公开论文金集、双人盲评一致性、模型/费用/长文矩阵仍待完成。

## 不可变正文、Rubric v3、盲评包与复评一致性

- 完成内容：quality report 升至 v2；每个终态 Run 内嵌完整 primary/paired 正文、逐节 hash/字符数、modified 标志、post-agent snapshot id、整体 manuscript fingerprint，以及被引来源冻结摘要/hash。Rubric v3 同时绑定 quality 与 manuscript fingerprint；缺失、篡改和 stale form 均由服务端拒绝。
- 盲评：桌面全屏读取冻结正文；blind packet 删除 run/project/model/pipeline/reviewer/既有决定和本地 PDF path，analysis packet 恢复 provenance/成本/评审；两类包带自身 fingerprint，原子写入项目 `exports/reviews/`。
- 复评：summary v2 对不同具名 reviewer 的同 Run 无序配对计算 exact decision agreement、decision kappa、六维 MAD、within-one 与 quadratic weighted kappa；同一人重复提交不冒充独立复评。
- 该评审阶段验证：315 collected；离线 `312 passed, 3 deselected`，显式 live `315 passed`；Electron `1 passed`（约 1.0m）覆盖冻结正文→blind export→两位 reviewer→agreement→无模型重启；已被本文件顶部当前构建证据替代。
- 该阶段发布：安装器 135,038,721 bytes，SHA-256 `0AFAB72A8DF1DED6378E7ECE41996564C3443A808F01072AE4A54AEEA657349B`；OpenAPI 134/153；已被顶部当前安装器替代。
- 遗留：尚未由真实专家执行随机化双盲金集，未校准自动 gate/模型阈值；本机视觉盲评不是带账号和任务分配的完整试验平台。

## LLM/Agent 外部故障恢复闭环

- 完成内容：统一 13 类 LLM outcome 与 error/retry/HTTP/retry-after/hint；严格校验 OpenAI/Anthropic/Gemini/Ollama terminal event；transport、JSON parse、stream、embedding 和意外异常均进入单行 usage 台账；断流 partial output 保存到失败 step；Step/Run/Job/SSE 状态和 diagnosis 一致；run 保存原 request、双 snapshot 与 retry/restore 元数据；桌面失败后刷新持久状态并提供重试、快照恢复和设置入口。
- 涉及文件：`core/errors.py`、`core/jobs.py`、`llm/base.py`、`llm/backends.py`、`llm/client.py`、`agents/base.py`、`agents/orchestrator.py`、`store/runs.py`、桌面 events/store/types/endpoints/Agents/Settings、`test_llm_faults.py`、Electron E2E。
- 验证：16 个离线故障测试及 Writer 断流 E2E；该阶段全量 304 offline/307 live，当前数字见 v3 任务。
- 部署：已重打并通过 installer E2E；冻结后端隔离 `--check` 为 `.env (none found)` / `no problems found`。
- 遗留：真实模型论文质量、真实计费与供应商低频 live 仍需专项验收。

## 手稿 DB/磁盘冲突保护

- 完成内容：文档级 DB/磁盘双摘要基线、8 类同步状态、普通操作拒绝覆盖、409 合同、Editor 冲突横幅与双向显式解决、强制操作的被覆盖侧备份；Git discard 增加 DB snapshot、磁盘副本、binary patch 和完成后 reindex。
- 涉及文件：`store/documents.py`、writing/versions routes、`vcs/git.py`、`vcs/versions.py`、Agent orchestrator、Overleaf、Editor/API types/endpoints。
- 验证：上述冲突/恢复合同与 Electron 链通过；该阶段全量 304 offline/307 live，当前数字见 v3 任务。
- 部署：2026-07-28 已重新生成 NSIS，冻结后端 `--check` 无问题。
- 遗留：当前已支持不同章节的安全合并，但同节双改、增删/重命名和旧 v1 基线仍无内容级三方 merge；真实远端认证和人工分叉解决仍缺 Electron E2E。

## Remote Git 桌面安全闭环

- 完成内容：Versions UI 增加命名 remote 配置、脱敏地址显示、Fetch、Pull (ff-only) 和非强推 Push；后端新增 remote sync 状态、fetch 与 fast-forward。Pull 只接受非 detached 当前分支、干净树和可快进历史；分叉不自动 merge。
- 涉及文件：`vcs/git.py`、`api/routes/versions.py`、`api/types.ts`、`api/endpoints.ts`、`VersionsView.tsx`、`test_git_remote.py`、`test_api.py`、Electron E2E。
- 数据安全：Fetch 不改工作树；真正 Pull 前 `ensure_sync_safe(reindex)`、DB snapshot 与磁盘手稿备份，成功后 reindex DB；URL/命令/错误统一脱敏，credential prompt 禁用，Push 永不 force。
- 验证：5 个本地 bare remote 合同覆盖中文/空格路径、首次 push、fetch/快进、脏树拒绝、凭据不回显和分叉/non-fast-forward 不改本地文件；API 合同验证 recovery/reindex；真实 Electron UI 用协作者 clone 完成配置→Push→协作者 commit→Fetch 不改文件→Pull recovery/reindex→再次 Push，并从同一基线制造双方提交，验证 ↑1/↓1/diverged、Pull 409、本地文件保留和远端文件未进入工作树。
- 遗留：未用真实 GitHub/GitLab、SSH 或 Credential Manager；没有内置分叉解决器，需外部 Git 客户端显式处理后再 Fetch。

## 真实 Electron E2E 研究、Agent、写作、版本与导出链

- 完成内容：真实 Electron main/Renderer/Python 核心链，并扩展到 Markdown、DOCX、LaTeX、cited-only BibTeX、bundle ZIP、Overleaf ZIP 的内容/结构与重启历史；修复 citation key fixture 大小写错误和 LaTeX `\cite` 被文本 escaper 二次转义。
- 涉及文件：`apps/desktop/e2e/`、桌面/root `package.json`、`package-lock.json`、`electron/main.cjs`、`src/state/store.ts`、`src/views/AgentsView.tsx`、`agents/base.py`、`agents/roles.py`、`agents/orchestrator.py`、`store/runs.py`、后端 tests、`.gitignore`。
- 验证：该阶段 Electron `1 passed`（约 1.0 分钟）；当前已扩展 v3/盲评/双评，见上方任务。
- 部署：该 v2 安装器已被上方 v3 当前发行物替代；仍未签名。

## PaperCreator 品牌图标与普通权限打包

- 完成内容：新增可审计 SVG 母版、确定性 Electron PNG 渲染器、窗口 icon、Windows/NSIS icon 和 EXE 版本资源；用项目锁定 `rcedit` afterPack 替代需要解压跨平台符号链接的 winCodeSign executable editing。
- 涉及文件：`apps/desktop/assets/brand/`、`scripts/render-brand-assets.cjs`、`scripts/after-pack.cjs`、`electron/main.cjs`、desktop package 与 lockfile。
- 验证：1024×1024 PNG 四角 alpha=0、中心 alpha=255；应用与安装器提取图标哈希一致；ProductName/description/version 正确；全新空 builder cache 只下载 NSIS 即成功打包；正式 `npm run package` 与完整 Electron E2E 通过。
- 遗留：视觉商标注册/法律检索未做；代码签名证书和 clean VM 仍未完成。
- 遗留：公网 Provider 检索 UI、真实 Provider/模型 Agent 质量、旧项目无基线冲突、真实远端认证/分叉解决、PDF 本地 TeX 编译、真实 Overleaf Git、首次原生工作台 dialog 和 clean VM 仍需扩展自动覆盖；六类无外部依赖导出、Remote Git 本地安全闭环、installer 本机自动链已完成。旧 bundled check 临时工作台已安全删除；最终冻结检查临时目录也已清理。

## 项目级 LLM Wiki

- 完成内容：架构、状态、模块、API、配置、部署、系统/流程/参考/任务、风险与 Agent context。
- 验证：以代码/路由/配置/测试扫描构建；`npm run validate:docs` 可重复验证本地链接、空文档、围栏、UTF-8 replacement 字符和高置信密钥特征；当前源码 OpenAPI 独立复核为 157 paths/181 operations，并有 route snapshot。
- 遗留：后续代码变化必须持续同步。

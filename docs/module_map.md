> 文档用途：把业务模块映射到文件、上下游、输入输出与修改风险  
> 最后检查：2026-07-28  
> 对应代码：`backend/papercreator/`、`apps/desktop/src/`  
> 文档状态：基于当前代码整理

# 模块地图

| 模块 | 主要文件 | 上游 | 下游/输出 | 修改风险 |
|---|---|---|---|---|
| Electron 生命周期 | `electron/main.cjs`、`preload.cjs` | OS/用户 | 工作台选择/记忆、Bundled 后端、白名单 menu IPC、单行标题栏原生 overlay、窗口 | 极高：数据位置、安装、端口、权限、安全边界、系统窗口行为 |
| UI Shell/工作台首页 | `App.tsx`、`TitleBar.tsx`、`ActivityBar.tsx`、`Sidebar.tsx`、`WorkbenchPanel.tsx` | Renderer | VS Code 式单行菜单/项目/动作布局、10 个视图、7 类导入入口 | 高：分类语义、标题栏拖动/窗口控件预留、原生对话框和全局导航 |
| 前端状态 | `state/store.ts` | 视图、SSE | `api/endpoints.ts`、缓存状态 | 高：跨视图耦合和并发状态 |
| API 客户端 | `api/client.ts`、`events.ts`、`endpoints.ts` | Store/视图 | HTTP/SSE | 高：后端合同兼容 |
| 应用工厂 | `api/app.py`、`__main__.py` | Electron/CLI/Uvicorn | 中间件、路由、静态 UI | 高：启动和关闭顺序 |
| 配置/路径 | `core/config.py`、`core/paths.py` | Electron/所有模块 | 单根 Paths、workbench manifest、Settings | 极高：数据位置、可移动性与密钥层级 |
| 数据库 | `core/db.py` | Store/Jobs | SQLite schema v6、FTS5；v3 双语目标、v4 提示词、v5 助手对话、v6 归档导入来源映射 | 极高：格式变更需追加迁移 |
| 文档提取/手稿导入/翻译 | `importers/document_text.py`、`writing/manuscript_import.py`、`writing/translation.py`、Writing routes、`EditorView.tsx` | Workbench/Editor/Job/LLM | 多格式提取；导入 SHA-256 preview/apply；MyMemory/LLM durable 翻译预览、取消、陈旧拒绝、快照/一次应用 | 高：隐私、公共限流、OCR/格式损失、恢复、路径 containment |
| 投稿模板 | `writing/templates.py`、`writing/venue_templates.py`、Writing routes、`EditorView.tsx` | Projects/Editor | 11 个原创结构；用户授权 ZIP 静态检查、审计解压 | 高：许可证、ZIP bomb/path traversal、不可执行第三方文件 |
| 项目助手/提示词 | `api/routes/assistant.py`、`api/routes/prompts.py`、`store/assistant_chat.py`、`store/prompts.py`、`AssistantPanel.tsx` | TitleBar/Editor/LLM/Skills/Versions/Electron IPC | 持久上下文聊天、统计/导出/保留与预览删除、确认动作、模板 CRUD/变量 | 高：prompt injection、敏感上下文、删除范围、费用、写入确认、不得自动 push |
| 作业/事件 | `core/jobs.py`、`core/events.py`、`core/errors.py` | 搜索/分析/Agent/API | jobs 表、统一 failure diagnosis、SSE | 高：取消、失败终态、线程、重启；Run/Job/SSE 不得分叉 |
| 新论文项目 | `store/projects.py`、`api/routes/projects.py` | UI | project row、`projects/<slug>` scaffold | 极高：不能越界删除 |
| 分类输入资源 | `store/resources.py`、`api/routes/workbench.py`、`api/app.py` | Workbench/Library UI、JobManager | 同步文件/Idea；后台目录 inventory、空间预检、link/reparse/special 排除、TOCTOU 复核、分块摘要、原子托管副本、audit、workbench_resources、Paper/collection 关联、stale staging 回收 | 极高：复制边界、密钥排除、取消/清理、最后登记和删除保护 |
| 文献库 | `store/papers.py`、`api/routes/library.py` | 检索/UI | papers/FTS/collections/PDF | 高：合并不能覆盖用户字段 |
| 检索 | `core/models.py::SearchRequest`、`api/routes/search.py`、`retrieval/pipeline.py`、`registry.py`、`providers/*` | `SearchView`/Agent | Paper、SearchResponse、search history | 高：请求字段须贯穿 UI/Job/history/rerun；限流、去重、外部合同 |
| 分析 | `analysis/pipeline.py`、`embeddings.py`、`reduce.py`、`cluster.py`、`keywords.py`、`heatmap.py`、`gaps.py`、`graph.py`、`incremental.py` | Analysis API | AnalysisResult、points、模型状态 | 高：算法可解释性和增量兼容 |
| LLM | `llm/registry.py`、`base.py`、`backends.py`、`client.py`、`core/errors.py` | Agent/查询扩展/Skill 草稿 | 流式文本/JSON、结构化 diagnosis、durable usage/partial output | 极高：terminal event、首 delta 后不得重放、密钥、费用、失败计量 |
| Agents/评审 | `agents/orchestrator.py`、`quality.py`、`review.py`、`store/runs.py`、`api/routes/agents.py`、`AgentsView.tsx` | Agent API / 桌面 | quality v2、不可变全稿、Rubric v3 双指纹、blind/analysis packets、append-only reviews、agreement、snapshots | 极高：正文/摘要隐私、blind non-disclosure、v1/v2/v3 兼容、accepted 完整性、不得把 hash/人工结论当事实真值 |
| 手稿 | `store/documents.py`、`writing/manuscript.py`、`citations.py` | Editor/Agent/Export/VCS/Overleaf | DB sections + Markdown/BibTeX + sync baseline/conflict backups | 极高：任何写入口必须走方向预检，不能绕过基线 |
| 导出 | `convert/*`、`api/routes/export.py` | Export UI | Markdown/LaTeX/DOCX/BibTeX/ZIP/PDF | 高：格式损失和工具调用 |
| Skill | `skills/model.py`、`loader.py`、`runner.py` | Skills UI/Agent | prompt 注入、SKILL.md | 中高：优先级和 prompt 风险 |
| 版本 | `vcs/git.py`、`versions.py`、`store/snapshots.py`、`api/routes/versions.py`、`VersionsView.tsx` | Versions UI/Agent/保存/remote 协作 | 默认本地 Git status/init/commit/discard/branch/checkout；显式 remote/fetch/ff-only pull/push/remove；snapshot/restore | 极高：本地 commit 不得暗含 push；移除 remote 必须保留历史；恢复/切换/pull 必须保持 clean/ff-only + recovery + DB reindex；不得引入 force/自动 merge |
| Windows 打包/安装验收 | `scripts/backend_entry.py`、`build-backend.mjs`、`installer-smoke.mjs`、桌面 `package.json` | `npm run package` / `npm run test:installer` | PyInstaller onedir、ASAR、NSIS、真实已安装 Electron/bundled backend、升级/卸载 | 极高：冻结态 imports、资源路径、签名、注册表和数据保留 |
| 日志 | `core/logging_setup.py` | 所有模块 | main/errors log | 高：不得泄密 |

## 公共模型

`core/models.py` 中的 Pydantic 模型是模块间合同：`Author`、`Paper`、`WorkbenchResource`、`SearchRequest/Response`、`AnalysisConfig/Result`、`PaperPoint`、`GapCandidate`、`PositionResult`、`SectionModel`、`DocumentModel`、`ProjectModel`。字段变更需要同时检查数据库序列化、API OpenAPI、前端 `api/types.ts`、导入/导出和迁移。

## 关键调用链

- 检索：`SearchView` 挂载 → `GET /api/search/providers` 动态刷新；关键词/Idea/文献库论文 seed → Store → `POST /api/search` → JobManager → `retrieval.pipeline` → Providers → `store.papers`/history → Search 历史表；rerun 从历史 `SearchRequest` 建新任务。`use_llm_expansion` 必须贯穿整条链，不能在 `SearchBody.to_request()` 或后台默认值处丢失。
- 分类导入：Idea/普通文件 → `POST /api/workbench/resources` → 同步托管/摘要 → DB；代码、数据集、补充材料、待分类目录 → 原生目录选择 → `POST /api/workbench/resources/import` → JobManager → inventory/空间/link/TOCTOU 检查 → `.partial-res_*` 分块复制/摘要 → 原子 rename → DB 最后登记 → 可选 Paper/Collection。`waitForJob` 以 SSE + durable Job 轮询等待，取消/失败清 staging，启动回收严格命名残留。
- 图谱：`LandscapeView` → `/api/analysis` → `analysis.pipeline` → `store.analyses` → Three.js。
- 写作：`AgentsView` → `/api/agents/run` → JobManager → orchestrator → roles → `llm.client` → durable usage → 最终正文 citation metadata → `agents.quality` → documents/run/snapshots。Run detail 展示自动证据和逐节/逐篇核对；`POST .../evaluations` 先构建 review target、校验 accepted 全合同，再经 `store/runs.py` 事务追加 Rubric；`GET .../evaluations/summary` 读取每 Run 最新决定并另计 append-only 分歧历史。失败由 `core/errors.py` 规范化并传播到 Step/Run/Job/SSE；首 delta 后断流只保存 step partial output，run 保留原 request 与 snapshots，可重试或恢复。无 LLM 只禁用新 Run，不能遮蔽这些持久历史。
- 编辑：`EditorView` → CodeMirror → section PATCH；并轮询 sync-status → SQLite/磁盘摘要比较 → 安全 flush 或 409 横幅 → 显式选边与恢复副本。外部正文 reconciliation 以 CodeMirror annotation 区分用户输入；选边后只清除与权威侧一致的 dirty，保留并发新输入。
- 导出/版本：项目文件和数据库章节在操作前必须明确同步方向。

更细的类/函数入口见 [reference/file_reference.md](reference/file_reference.md)。

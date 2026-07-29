> 文档用途：关键文件与修改入口索引  
> 最后检查：2026-07-28

# 文件索引

| 文件 | 关键入口 | 修改前关注 |
|---|---|---|
| `package.json` | workspace scripts | 跨平台命令和 Node 版本 |
| `scripts/setup.mjs` | `findSystemPython/run/main` | Windows 不使用 shell 拼接参数 |
| `scripts/run-tests.mjs` | isolated home + pytest spawn | `-m "not live"` 必须作为单参数 |
| `scripts/build-backend.mjs` | PyInstaller onedir orchestration | 只清理已验证 build 子目录；资源/hidden import |
| `scripts/backend_entry.py` | frozen entry | `sys.frozen`、UTF-8、check/serve |
| `scripts/installer-smoke.mjs` | 最终 NSIS install/upgrade/uninstall E2E | 先拒绝已有用户安装；只清理校验过的系统临时根；等待 NSIS 异步注册清除 |
| `apps/desktop/electron/main.cjs` | workbench selection/startBackend/createWindow | userData 设置时机、dev/packaged path、子进程清理、安全 |
| `apps/desktop/assets/brand/icon.svg` | PaperCreator 品牌母版 | 保持小尺寸可读、无文字、透明边界；PNG 只能由母版重建 |
| `apps/desktop/scripts/render-brand-assets.cjs` | SVG→1024 PNG | 必须移除 `ELECTRON_RUN_AS_NODE`；验证 alpha/尺寸 |
| `apps/desktop/scripts/after-pack.cjs` | Windows EXE icon/version stamping | 保持 `signAndEditExecutable=false`；`rcedit` 版本需锁定并验证普通权限 clean cache |
| `electron/preload.cjs` | contextBridge API | 只暴露最小 IPC |
| `src/App.tsx` | `renderView` | Shell/错误恢复 |
| `src/state/store.ts` | `useStore` actions | 全局并发状态 |
| `src/api/endpoints.ts` | `endpoints` | 与 routes/OpenAPI 一致 |
| `src/api/events.ts` | SSE/reconnect | replay seq/重复订阅 |
| `src/components/Landscape3D.tsx` | Three scene | dispose、选择、性能 |
| `src/components/CodeEditor.tsx` | CodeMirror lifecycle | 用户输入/save/中文输入；外部 value 用 transaction annotation，禁止误触发 dirty/undo |
| `src/components/WorkbenchPanel.tsx` | 分类创建/导入入口 | Project 与 Resource 语义、原生 dialog、长复制 |
| `backend/papercreator/__main__.py` | `main/run_check` | CLI/诊断合同 |
| `api/app.py` | `create_app/lifespan` | 初始化顺序/关闭/路由/static |
| `core/config.py` | `build_settings/reload_settings` | 配置优先级/secret |
| `core/paths.py` | `get_paths` | 唯一路径源 |
| `core/db.py` | schema v6/`init_db` | v1-v6 迁移只追加、事务和 FTS |
| `importers/document_text.py` | 多格式文本提取 | PDF/DOCX/MD/TXT/TeX、截断与告警 |
| `writing/manuscript_import.py` | 手稿两阶段导入 | SHA-256、拆节、append/replace、snapshot/审计副本 |
| `writing/translation.py` | 翻译边界 | 离线术语；MyMemory 分块/限速/重试/取消；LLM 翻译；不直接写手稿 |
| `writing/venue_templates.py` | 投稿 ZIP 安全导入 | 路径/链接/加密/大小/许可证确认与 manifest |
| `api/routes/assistant.py` | 项目感知只读聊天与治理 API | 有限上下文、Skill、建议动作；统计/导出/preview-token 删除；不得执行建议动作 |
| `store/assistant_chat.py` | DB v6 对话权威 | 工作台/项目作用域、严格 ordering、完整导入导出、来源幂等、保留/消息清理预览与事务执行 |
| `store/prompts.py` | 提示词模板权威 | 工作台/项目作用域、变量解析、CRUD |
| `components/AssistantPanel.tsx` | 右侧 AI/提示词/Skill 确认 UI | 回答纯文本，写手稿/Git/Skill 二次确认 |
| `core/jobs.py` | `JobManager` | thread pool/restart/cancel |
| `core/events.py` | `EventBus`/SSE | 有界 replay |
| `core/models.py` | Pydantic contracts | DB/API/TS/导出联动 |
| `retrieval/pipeline.py` | `search_async/search` | 部分成功、持久化、progress |
| `retrieval/base.py` | Provider contract | metadata/rate limit/safe_search |
| `analysis/pipeline.py` | `build_analysis` | 阶段顺序/回退/保存状态 |
| `analysis/embeddings.py` | backend resolution/cache | 模型网络/维度/兼容 |
| `analysis/incremental.py` | placement | reducer transform 可用性 |
| `analysis/gaps.py` | detectors | caveat/证据，不夸大 |
| `llm/backends.py` | 4 backend classes | 协议/stream/error/secret |
| `agents/orchestrator.py` | `Orchestrator/submit_run` | snapshot/budget/partial writes |
| `agents/roles.py` | 11 Agent classes | prompt/schema/citation |
| `store/documents.py` | `sync_status`、`ensure_sync_safe`、flush/reindex、`backup_sync_side` | 双摘要基线、方向保护、文件 ownership 和恢复副本；最高风险 |
| `api/routes/writing.py`、`EditorView.tsx` | sync-status/force API、`ManuscriptSyncBanner` | 409 合同、显式选边、dirty editor 与轮询状态 |
| `vcs/git.py`、`api/routes/versions.py` | `backup_worktree_patch`、checkout/discard/restore | Git 修改前预检、snapshot/patch、完成后 reindex |
| `store/papers.py` | upsert/merge/search | 用户字段保护/FTS |
| `store/resources.py` | import/copy/hash/delete | library containment、secret 排除、失败残留 |
| `api/routes/workbench.py` | workbench/resources/state | API/TS/UI/DB 分类合同 |
| `convert/exporters.py` | format dispatch/PDF | subprocess/path/warnings |
| `vcs/git.py` | scoped command wrappers | destructive scope/credentials |
| `skills/loader.py` | discover/sync/CRUD | precedence/builtin read-only |
| `backend/tests/` | 246 tests | 临时 home；live 网络显式选择；workbench 测试不得触碰真实数据 |

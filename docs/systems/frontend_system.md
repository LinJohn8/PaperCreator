> 文档用途：Electron + React 前端专项说明  
> 最后检查：2026-07-28  
> 对应代码：`apps/desktop/electron/`、`apps/desktop/src/`  
> 文档状态：部分可用

# 前端系统

## 结构

Electron main 管理首次/切换工作台、单实例、后端子进程、原生菜单、文件对话框和外链；preload 以 contextBridge 暴露窄 IPC；Renderer 开启 `contextIsolation`、关闭 `nodeIntegration`。生产 renderer 从 ASAR 加载构建结果，开发从 Vite `5173` 加载。

React 不使用 URL router，而由 Zustand `ViewId` 切换 10 个视图：Projects、Search、Library、Landscape、Editor、Agents、Versions、Export、Skills、Settings。需要项目的视图在未打开项目时给出解释而不是隐藏。

启动语言从工作台设置恢复，`zh-CN` 和 `en-US` 只显示当前一种界面语言；Provider 名、Overleaf、IEEE、API 等专名可保留原文。Settings 和命令面板都调用 `store.setLocale()` 写入后端，不能只改 Renderer 内存。普通 success/info/warning 通知分别约 1.6/2.4/3.8 秒消散；错误保留到用户关闭。第二实例在主窗口未完成创建时会等待可用窗口，再按当前语言询问打开检索、选择其他工作台或取消。

全新空工作台在后端、项目和工作台分类状态加载完成后自动显示任务型快速开始。五步进度来自真实工作台状态，不使用手工勾选或 Chromium localStorage；`ui.quick_start_version` 只记录用户明确关闭自动显示。Help 原生菜单和命令面板都可重新打开。LLM 设置与导出作为可选下一步，不把无模型的合法离线使用判为未完成。步骤动作复用 Store 的导航和全局项目创建状态，因此原生菜单、引导和页面按钮不会分叉成不同流程。

`WorkbenchPanel.tsx` 位于 Projects 首页上方，明确区分“新论文”与 7 类输入资料。它调用原生文件/目录选择对话框，再由后端创建托管副本；代码、数据集、补充材料和待分类目录会启动 `resource_import` Job。导入弹窗显示百分比、阶段消息和 Job id，支持 cooperative“取消导入”与“转入后台”；转后台后仍可在 Output → Jobs 查看进度或取消。最近三项可直接从 Explorer 打开。切换工作台会提示重启且不搬迁旧数据。

项目卡使用独立大网格，显示文献数、主稿字数、章节数、模板、语言、双语、本地 Git 和更新时间；创建或点击项目直接进入该项目手稿。标题栏项目 pill 是当前项目身份的权威 UI，不再依赖 Projects 页卡片是否可见。

`SearchView.tsx` 提供关键词、我的 Idea、已有论文三种 seed 入口。页面挂载时主动刷新 Provider 可用性；进入已有论文模式时按当前项目加载文献库，下拉选择后用标题和摘要填充仍可编辑的输入。Provider 默认值只初始化一次，因此用户可以清空网络源、只选择刚导入后可用的 `local`。页面将 `use_llm_expansion` 连同真实搜索参数提交给后台 Job，并显示最近 20 条项目/全局检索历史；历史行展示模式、seed/query、来源、结果数、时间与规则展开标记，“重新检索”建立新记录。

检索源 chip/checkbox 提交真实 `enabled_providers`，禁止关闭最后一个源；不再向通用 settings PATCH 发送空对象，因此不会出现 `saving settings: no settings were provided`。文献库自有论文卡会展示提取方法、字符数、截断和 OCR/损坏告警。

`EditorView.tsx` 将文章级动作放在手稿顶部：导入 PDF/DOCX/MD/TXT/TeX、Markdown/Word/LaTeX/Overleaf ZIP 导出、投稿模板包导入、进入完整导出页和打开项目文件夹。章节栏提供 CRUD/排序/双目标；编辑区对照文本是本地草稿并显式保存。术语选择、单节翻译和批量翻译都展示 Provider 的隐私/费用边界；MyMemory 还要求显式外发确认。长文/批量通过 Job 显示进度/取消，完整预览可从 durable Job 恢复，用户复核并再次勾选后才一次写入全部章节。

章节弹窗的 label/id 必须保持可访问关联。Store `reloadDocument()` 会校验 `activeSectionKey`：当前 key 在新文档中不存在时选择首节，并移除只指向已删除章节的 dirty entry；这同时保护删除章节、Git checkout、snapshot restore 和外部 reindex 后的编辑器可用性。

`AssistantPanel.tsx` 是可折叠右栏。聊天带当前项目、章节、文献样本和启用 Skill；DB v6 对话按工作台/项目恢复。治理弹窗显示范围统计，可通过 Electron 受限 IPC 保存 JSON/JSON.GZ、定位文件和选择归档；Renderer 没有任意文件读取能力。导入、保留批删和单条消息不可逆脱敏都必须先预览再确认，历史 actions 仍只是建议。建议动作在前端二次确认后，才可追加到当前未保存草稿、保存项目 Skill 或创建仅本地 Git commit；不会自动 push。`PromptTemplatesDialog` 管理工作台/项目模板、搜索、复制粘贴和 `{{variable}}` 填充。

## 状态与通信

- `state/store.ts` 保存项目、文献、分析、章节、jobs、Agent、健康、locale 和 UI 状态；跨视图业务动作集中于此。
- 最近打开项目写入工作台 DB `app_state`，不再以 Chromium localStorage 作为权威。
- `api/endpoints.ts` 是唯一推荐的路径封装；`api/client.ts` 处理 URL/JSON/error；`api/events.ts` 管理 SSE replay/reconnect。`JobFailureError` 保留 `job.failed` 的机器 diagnosis，不把所有后台故障压平成普通字符串。`waitForJob()` 先订阅 SSE、再读取 durable Job，并以每秒轮询兜底，因此小任务在订阅前完成、Renderer 重连或事件缓冲丢失都不会永久等待。
- UI 通过 SSE 接收进度，并由 StatusBar 8 秒轮询 jobs、OutputPanel 4 秒轮询日志兜底。`job.created/progress/done/failed` 刷新全局 Jobs；`resource_import` 完成后刷新工作台资源、Library 和当前文档。
- 编辑器用 CodeMirror，图谱用 Three.js；两者为 bundle 最大 chunk。

## 当前验证与限制

Windows 标题栏由 Renderer 绘制单行 VS Code 式信息架构，Electron `titleBarOverlay` 保留原生窗口控件/Snap Layout。品牌图标位于 File 左侧，File/Edit/View/Help 通过白名单 `menu:popup` IPC 打开仍注册快捷键的原生菜单模型；项目身份居中，命令/连接状态位于右侧并为原生窗口控件预留 138px。启动和后端故障态同样渲染标题栏，因此窗口始终可拖动。非 Windows 继续使用系统标题栏。

`npm run build` 已通过（95 modules）。Playwright Electron 除原有核心链外，验证快速开始首启、真实进度、Help/命令面板重开、偏好 round-trip、重启不自动出现、菜单新建、日志 IPC、Tab 焦点以及 1365×900/1100×700 截图边界；继续覆盖单语言、项目恢复、章节 CRUD、提示词、助手、Git、六类导出和四次 Electron/后端启动。最终断言 FastAPI shutdown、SQLite WAL checkpoint 与后端退出码 0。最新 `1 passed`（1.6 分钟）。blind mode 只能减少锚定偏差，不证明真实随机化双盲；fixture 的双评与 κ 只验证产品合同。

自动化环境通过 `PC_E2E=1` 启用软件/进程内 GPU，绕开当前 Windows Agent 会话中 Chromium GPU 子进程的 `0xC0000135`；正式安装版不受影响。后端重启以 child 实例作为状态所有者，旧 child 的迟到 exit 不能清空新 child；Windows 原生关闭先由 lifecycle IPC 保存 Renderer dirty sections，失败/超时取消退出；成功后以不暴露给 Renderer 的随机 capability 请求 Uvicorn 优雅退出，断开 SSE 并等待 checkpoint/进程退出。`taskkill /T /F` 只在 owned child 超时后兜底。品牌渲染脚本同样使用独立临时 userData/session/cache 和软件渲染，父 Node 进程在 Electron 完全退出后执行最终清理，避免读取、污染用户全局 Chromium 状态或留下锁定缓存。

Agent Job 成功后 Store 刷新 run/document/timeline；失败后也先刷新持久状态，再显示 outcome/hint。Run detail 除 Provider/model/快照/恢复外，还展示自动 gate、metrics、逐项检查和“不能证明事实”的边界；人工表单列出每个 modified section 与每篇 cited Paper，提供来源 URL/PDF 打开入口、warn acknowledgement、六项分数、reviewer、decision 和 evidence notes。accepted 前端预检会列出具体 blocker，但后端仍是唯一权威。历史项展示 rubric version、证据 fingerprint 和节/来源数量；摘要面板展示 reviewed/evaluation/multi-review/disagreement 及维度、pipeline/model 统计。移除 LLM key/base URL 后只把 Run launcher 替换成“未配置模型”说明，summary、run history、quality report 与 human evaluations 继续加载。评审历史和失败 Audit 重启后都来自 SQLite，而非内存 toast/SSE。

品牌资产以 `assets/brand/icon.svg` 为可审计母版，`npm run brand:build` 用隔离 Electron 渲染 1024×1024 alpha PNG，并主动移除宿主 `ELECTRON_RUN_AS_NODE`。BrowserWindow 开发态直接引用 PNG；打包态 `afterPack.cjs` 用锁定的 `rcedit@4.0.1` 写入应用 EXE，NSIS 使用同一转换 ICO。该路径不依赖 electron-builder 的 `winCodeSign` 跨平台压缩包，因此普通 Windows 用户关闭 Developer Mode 时也能构建。

独立 `scripts/installer-smoke.mjs` 已用 Playwright 启动最终安装得到的 EXE，自动覆盖 bundled backend、七类工作台、创建/升级/重启恢复和卸载保留数据。当前仍没有 Vitest/React Testing Library；源码 Electron 主链已覆盖学术 429 和 LLM 断流的桌面故障、六类导出和本地 bare remote 的完整 Remote Git UI，但首次原生工作台选择、键盘菜单、逐个真实公网 Provider、真实 Provider/模型论文质量、真实 GitHub/GitLab/SSH 认证与人工分叉解决、PDF 本地 TeX 编译和真实 Overleaf Git 没有自动覆盖；installer 仍缺 clean VM/签名矩阵。`store.ts` 和 Settings/Landscape 等视图偏大，修改需回归跨视图状态。

确认前端修改：开发态看 Vite 热更新；生产态必须重新 `npm run package` 或至少 build 后启动正确产物，Electron 不读取 TSX 源码。Chromium userData/cache/local storage 位于当前 `.papercreator/electron/`；AppData 只有工作台定位指针。

> 文档用途：测试框架、命令、隔离、覆盖和发布检查  
> 最后检查：2026-07-28  
> 对应代码：`backend/tests/`、`apps/desktop/e2e/`、`scripts/run-tests.mjs`、`scripts/installer-smoke.mjs`  
> 文档状态：后端较完整；真实 Electron E2E 基线已建立，组件/完整 UI/洁净机仍不足

# 测试指南

## 当前证据

`npm run test:backend` 使用随机临时 `PAPERCREATOR_HOME`。2026-07-28 当前代码：360 collected；本轮非联网 `357 passed, 3 deselected in 28.76s`。3 项 deselected 是显式 live 检索，本轮没有重复调用公共服务。新增覆盖快速开始设置默认/patch/health round-trip；既有 DB v6、OCR、sync manifest v2、复杂 DOCX、CLI endpoint 和 OpenAPI 157/181 route snapshot 合同保持通过。

桌面 locator 只接受当前一种语言，不再依赖“中文 · English”拼接；创建项目后断言标题栏项目身份和 Editor。通用 launch 只等待非 transient shell。当前长链另验证 Assistant JSON/JSON.GZ 受限归档、导入新建/跳过、消息不可逆脱敏，translation preview→apply，以及逐章节非重叠合并。计划继续拆分三条短场景：locale/search settings；writing import/translation/CRUD；assistant/prompts/local Git confirmation。

覆盖：core normalization/config/DB/events/jobs/log scrub；papers/projects/documents/resources/snapshots/cache；单根工作台目录、分类说明、托管复制、密钥排除、最近项目 DB 状态；检索去重/排名/查询展开/Provider parser/registry；分析各阶段/full pipeline/incremental/graph；转换/引用/DOCX/LaTeX/export；FastAPI 主要合同和破坏性保护。手稿专项覆盖 DB/disk 单侧与双侧变化、无基线旧项目、force backup、API 409、Git checkout/discard、snapshot restore、Agent/Overleaf 修改前保护；Agent 专项还验证一步内多次 LLM prompt 原子追加、`_persist()` 只写本轮 dirty sections、prompt/export citation collision 一致、分章节子集保持项目级 key、最终正文重建 citation metadata、自动结构检查与模型 warning 分离，以及失败 Step/Run/Job/usage/双快照合同。

`test_agent_quality.py` 覆盖 quality v2、逐节 primary/paired hash、整体 fingerprint 稳定、正文变化和篡改检测、legacy v2 target。`test_review_evidence.py` 覆盖 blind packet 不泄漏 identity/model/prompt/output/local path、analysis provenance 恢复、项目内 export、stale/篡改不可 accepted，以及 distinct-reviewer agreement 数学合同。API 继续覆盖 gate/目标/notes/来源等 blockers。fixture 评审只证明合同，不宣称论文事实正确。

`backend/tests/test_llm_faults.py` 有 16 个完全离线测试：MockTransport 覆盖 401、429+Retry-After、503、其他 4xx、timeout、connect、200 非 JSON、缺 choices 和空文本；OpenAI 坏 SSE 与无 `[DONE]`，Anthropic 无 `message_stop`，Gemini 无 finish reason，Ollama 无 `done=true`；JSON parse failure 必须把同一 usage row 改为失败，stream interruption 必须只写一条带 partial token 的失败 usage；失败 Agent 必须让 Step/Run/Job 均为 failed，保留 partial output、pre/post snapshots、retry hint 并可重新读取。

`backend/tests/test_workbench.py::TestSafeDirectoryImports` 有 9 项目录安全合同：成功导入与完整 audit；直接 cooperative cancel 后 staging 清理；空间不足；文件 TOCTOU 变化；空目录/目录节点变化；嵌套 link/reparse 分类且不跟随；启动 stale staging 精确回收且不碰相似用户名；同步文件端点拒绝目录/目录端点拒绝文件；API Job 取消终态与无 ready row。整个工作台专项当前为 17 passed。

`backend/tests/test_git_remote.py` 只创建系统临时目录内的 bare repository/peer clone：验证含中文和空格的 remote URL 不被空白切分、首次 push 建立 upstream、URL 内嵌凭据不会从返回值/命令诊断泄漏、fetch 能识别协作者提交但不改文件、干净树可快进、脏树/分叉被拒绝、non-fast-forward push 保留本地提交，以及 remove remote 后本地 HEAD/branch/files 不变。`test_api.py` 另验证 pull 写前 snapshot/磁盘备份、写后 DB reindex，以及 DELETE remote 只断开连接。它不证明 GitHub/GitLab 登录、SSH/Credential Manager 或人工分叉解决。

```powershell
npm run test:backend
npm run test:backend -- --live
npm run typecheck
npm run test:e2e
npm run build
npm run package
npm run test:installer
```

`smoke_retrieval.py`、`smoke_analysis.py` 是专项脚本，不等于发布 E2E。live 测试受网络、限流和服务变化影响，失败要区分代码/外部状态。

## Electron E2E 基线

`npm run test:e2e` 先生产构建 Renderer，再由 Playwright Electron 启动真实 `electron/main.cjs` 与真实 Python 后端。产品 API、数据库、Agent、文件写入和重启都不 mock；只有外部 LLM 供应商由进程内本地 OpenAI-compatible HTTP 服务提供确定性 JSON 与 SSE 响应，从而无需云 key 也能验证真实网络客户端和流式解析。每次建立系统临时目录 `papercreator-e2e-*`，以 `PAPERCREATOR_WORKBENCH` 指向该目录，使用随机回环端口和 `PC_OFFLINE_MODELS=1`，结束时核对路径边界后删除。宿主若设置 `ELECTRON_RUN_AS_NODE`，测试启动器会显式移除，避免 Electron 被当作 Node。

工作台子链先通过受限 `PC_E2E_OPEN_DIRECTORY` 穿过真实 Electron 原生目录 IPC，将隔离工作台内 65-file 代码源交给 `resource_import` Job/SSE。它断言 `.env` 和 `node_modules` 不进入托管副本、`.env.example` 保留，metadata audit 的计数/策略正确，Job 为 done 且分类目录没有 `.partial-res_*` 残留。环境变量只接受隔离工作台内部目录，不能用来读取任意宿主路径。

检索子链保持上述合同。当前整条主链为 `1 passed`，总计 1.5 分钟；除标题栏取证外，还覆盖章节 CRUD/双目标、Prompt 变量应用、助手归档/脱敏、逐章节合并、AI 动作确认、语言持久化、最后项目恢复和四次真实 Electron/后端启动。第三次启动在 CodeMirror 留下 dirty 文本后直接正常退出，第四次启动断言文本已经持久化，证明退出握手不是只测日志。

当前主链还验证快速开始首启/Help/命令面板/持久化/重启、Help 菜单模型、菜单新建项目、日志 IPC、Tab 焦点和 1365×900/1100×700 截图边界，以及 Windows 单行标题栏的 logo→File→右侧 command 几何顺序、隐藏原生第二菜单行与窄 menu bridge。Versions、评审和恢复链保持原有覆盖；fixture 只验证界面、持久化与数学合同，不是专家金集或论文质量结论。

Electron E2E 设置 `PC_E2E=1` 后使用软件/进程内 GPU，以隔离当前自动化会话的 Chromium GPU 子进程依赖故障；生产安装版不设置此变量并继续使用正常硬件加速。重启/退出场景验证旧 child 的异步退出不能覆盖新 child 状态；Electron 用随机 capability 请求隐藏回环关机路由，Renderer 退出时先关闭 SSE，随后断言 Uvicorn `Application shutdown complete`、WAL checkpoint 和 backend exit code 0。测试只在主动替换后端的局部窗口允许 Chromium 报告 reset/refused/incomplete-chunk，重启恢复后重新执行零控制台错误断言。Windows venv launcher 超时才使用精确 PID tree 的强制兜底。

测试临时目录清理先验证系统 temp 根和 `papercreator-e2e-*` basename，再对 Windows `EBUSY/EPERM/ENOTEMPTY` 做约 22 秒有界退避。关闭超时只终止 `ElectronApplication.process().pid` 的精确进程树并再次等待退出，避免 finally 吞错后留下 owned Python。独立 `npm run cleanup:e2e` 默认 dry-run，要求最小年龄和结构化边界；只有显式 `--apply` 才回收旧隔离目录。任何用户工作台都不在删除边界内。

无模型重启 fixture 必须把所有受支持 API key 和 `PC_OLLAMA_BASE_URL` 以空值保留在子进程环境中，不能简单 delete：后端 `.env` loader 按设计只补“环境中不存在”的键，delete 会让开发机私有 `.env` 污染 E2E。测试不读取或打印真实 key；空值只用于阻止继承/补载。

导出完成后产品通常调用 Explorer。`PC_E2E=1` 只把 `shell.showItem/openPath/openExternal` 变为无外部副作用的返回值，避免测试打开宿主程序；导出 API、后端 writer、项目 `exports/` 写入和 UI 文件列表仍全部是真实实现。`PC_E2E_OPEN_FILE` 仅在该模式下替代原生文件选择，并强制 fixture 位于隔离工作台内；正常开发/安装不走这些分支。

这条测试实际发现并约束了多类跨层竞态：`saveSection()` 将服务端章节与 dirty 清理原子更新；同步冲突“数据库优先”只清除与重载 DB 相等的 dirty，保留更晚输入；CodeMirror 外部 value reconciliation 使用 annotation 且不进入 undo history/用户 onChange；Agent 的 `ask()`/`ask_streaming()` 向当前 step 原子追加 prompt；Blackboard 只持久化本轮 `modified_section_keys`；run 完成后重新加载 history；质量 fixture 曾因虚构 `[TEST2026]` 被门禁正确判 fail，现改为真实导入的 `[RESEARCHER2012]`，没有放宽检查；重启断言等待标题栏“项目已打开”而不是仅等待项目卡片出现。Run history 与 Agent steps 表具有可访问名称，测试不会误匹配质量检查表或角色说明表。

## Windows 安装器 E2E

`npm run test:installer` 是 Windows-only 的真实安装链，不是 `win-unpacked` smoke。它先检查 HKCU 卸载注册并在发现既有 PaperCreator 安装时拒绝运行；随后只在系统临时目录创建 `papercreator-installer-e2e-*`，安装目录和工作台目录都包含中文与空格。Playwright 以最终安装得到的 `PaperCreator.exe` 启动应用，断言生产模式、bundled backend、Electron/FastAPI 工作台一致、七类 library 目录存在，并通过真实 API 创建 Idea 和新论文项目。

脚本关闭应用后在同一路径静默覆盖安装，重启确认 Idea、项目和 marker 均恢复；再静默卸载，等待 EXE 和 HKCU 注册项异步清除，同时确认 `.papercreator`、SQLite、Idea 和项目数据没有被卸载器删除。最后只在验证过的临时根内清理。2026-07-28 本机运行通过，且结束后没有安装注册、临时根或 PaperCreator/Electron/backend 进程残留。该证据仍不能替代全新 Win10/11 VM、标准用户/杀软矩阵或 Authenticode 验收。

## 缺口

- 无 React component/unit 或全应用 axe 扫描；快速开始已有两档真实截图、dialog 语义与键盘焦点断言，但尚未建立全视图像素基线；Electron E2E 仍集中在一条串行核心链。
- 本地文献、Idea/已有论文检索、OpenAlex 429 部分成功/诊断/定向恢复/历史/重启、3D 图谱、Idea 定位、确定性 Agent 正常与断流恢复、同步冲突、本地/Remote Git 和 installer 已进入自动 E2E；LLM HTTP 401/429/503/坏 JSON、timeout/network/四协议 terminal、usage 和失败持久化另有离线合同。尚缺逐个真实公网 Provider 的桌面 live 矩阵、真实模型质量、真实远端认证/人工分叉解决与首次原生工作台选择。
- 内置无外部依赖的六类导出已进入 E2E；PDF 仍需本机 TeX，真实 Overleaf Git bridge 与 venue/Word/Overleaf 最终排版仍需专项或人工验收。
- 已有 quality v2、Rubric v3 双 fingerprint、blind/analysis packet 和复评一致性算法/E2E fixture，但尚无真实 LLM 金集与专家双盲实验；fixture 的 κ=1 只证明实现可计算，不能作为产品质量结论。
- 本机自动 NSIS E2E 已通过；仍没有干净 Windows VM、标准用户/杀软矩阵或代码签名验收。
- 无真实 10GB/百万小文件/网络盘/超长路径/低空间卷/普通权限 junction 的目录导入基准；当前 65-file Electron 链与 9 个安全合同不能替代该矩阵。也无大规模 library/analysis 性能、并发 SQLite 和断电恢复测试。
- 导出只验证结构可打开，尚未逐 venue/Word/Overleaf 人工排版验收。

## 发布前最低检查

全后端 + live smoke（记录时间/Provider）+ 前端 build + packaged backend `--check` + 新建工作台/分类导入/项目/检索/图谱/手工章节/Agent（真实配置）/snapshot restore/四种核心导出 + 干净机安装 + 数据升级/卸载保留测试。

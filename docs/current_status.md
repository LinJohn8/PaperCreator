> 文档用途：回答“项目当前真实做到什么程度”  
> 最后检查：2026-07-29  
> 对应代码：整个仓库与本轮验证命令  
> 文档状态：当前状态快照

# 当前状态

## 模块快照

| 模块 | 状态 | 已实现 | 已知限制 / 下一步 |
|---|---|---|---|
| Electron 工作台 | 可用 | Windows 使用 VS Code 式单行自绘标题栏和 10 个业务视图；空工作台首启显示基于真实状态的五步快速开始，可稍后关闭、持久关闭自动显示，并从 Help/命令面板重开；步骤直达创建、资料、手稿、版本、设置和导出。菜单新建项目直开表单，日志入口使用真实 IPC；核心模态框有名称、字段关联、Escape 和 Tab 焦点循环；安装态后端故障页提供重启/日志/重装恢复而不暴露开发命令 | 首次工作台原生目录选择尚未单独自动化；多数旧业务模态框尚无统一组件级无障碍扫描；非 Windows 仍使用系统标题栏 |
| 工作台存储 | 可用 | 所选目录只生成 `.papercreator/`；项目与 7 类输入分离；目录导入走 Job/SSE，含预扫描、空间预检、进度/取消、`.partial-res_*` 同盘 staging、分块摘要、原子 rename、DB 最后登记和重启残留回收；symlink/reparse 不跟随；移动友好相对路径 | 尚未做真实 10GB/百万小文件、网络盘、超长路径和普通权限 junction 基准；跨盘移动需停应用后整体复制 |
| Windows 安装 | 部分可用 | PyInstaller onedir 后端、NSIS 安装器、无系统 Python 启动；PaperCreator SVG→PNG→ICO 可复现品牌链，应用/安装器图标及 ProductName/FileDescription/version 已写入；本机 installer E2E 自动验证中文/空格路径、安装、真实已安装应用、覆盖升级、恢复与卸载保留数据 | 尚未在全新 Win10/11 VM 验证；未做 Authenticode 代码签名 |
| FastAPI 后端 | 可用 | 当前源码 14 个业务 router、OpenAPI 157 路径/181 操作，并有 method/path SHA-256 路由快照；统一错误、CORS、SSE、日志、后台任务；CLI 实际 `--host/--port` 贯穿 app/lifespan/CORS/热重载；Electron 以每次启动随机 capability 调用隐藏关机路由并正常 checkpoint | 无用户鉴权；只应绑定回环地址；本轮未重打安装包，旧冻结后端不含本轮新增实现 |
| 项目/文献库 | 可用 | CRUD、集合、分类托管导入、FTS、去重合并、标签、PDF 任务 | PDF 下载依赖 OA URL；真实大库性能未压测 |
| 自动检索 | 可用 | 9 个 Provider、并发/限流/缓存/去重/RRF、idea/paper 模式；每源结果稳定分类为 success/unavailable/rate-limit/timeout/auth/HTTP/network/invalid/parser 等；全失败也保存请求与诊断，部分失败保留好结果；Search UI 显示中英建议并可仅重试可恢复来源；OpenAlex 支持安全镜像端点；429 Electron E2E 通过 | 默认仅启用 arXiv/OpenAlex/Crossref；本轮 live 中 arXiv 先 timeout、重试后 rate-limit，诊断正确但公网 SLA 未通过；尚无 nightly 趋势监控和各公网源逐一桌面 live 验收 |
| 3D 分析 | 可用 | 嵌入、3D 降维、聚类、关键词、热力、5 类缺口、图、增量定位/删除点；离线 Hashing+PCA 的 12→13→12→13 点链已有真实 UI E2E | 缺口是候选信号而非研究结论；少于 8 篇不做完整缺口分析；Hashing 只衡量词形重叠而非深层语义 |
| LLM 接入 | 实验性 | OpenAI-compatible、Anthropic、Gemini、Ollama；统一 outcome/error/retry/HTTP/hint；401/429/5xx/timeout/network/坏 JSON/空响应/断流/缺 terminal 均有离线合同；非流/流/embedding/JSON parse 失败正确记账；本地确定性 OpenAI-compatible HTTP/JSON/SSE 已穿过真实客户端 | 未用真实云 key 验收生成质量、实际费用、真实云限流和模型漂移 |
| 多 Agent | 实验性 | 11 角色；每个终态 Run 生成 quality report v2，并内嵌全稿双语不可变正文、逐节哈希、摘要哈希与整体 manuscript fingerprint；Rubric v3 accepted 强制绑定该版本；桌面可全屏盲评、导出 blind/analysis 两类包、双人复评并显示 exact agreement、decision kappa、MAD、within-one 与 quadratic weighted kappa | 旧 rubric v1/v2 原样可读，但无冻结正文的 Run 不能新增 v3 accepted；现有“盲评”是本机隐藏身份/导出基础设施，真实专家任务分配、随机化、公开金集和阈值校准仍未执行；自动/人工结论都不是绝对事实真值 |
| 写作/双语 | 可用 | 章节增删改名/排序/要求/状态；中英文独立目标与目录双进度；离线术语及 MyMemory/LLM 翻译；长文/批量翻译走持久 Job，完整预览校验双侧 SHA-256 后一次写入；全项目 citation registry；sync manifest v2 保存逐节指纹，DB/磁盘修改集合完全不重叠时可预览确认合并，并先建 DB snapshot 与两侧恢复副本 | MyMemory 文本离开本机且受公共额度/SLA 约束，必须逐次明确确认；单 Job 最多 100,000 字符/250 请求；运行中重启按通用 Job 规则失败；同节双改、增删/重命名和旧 v1 基线仍须显式选边；无 CRDT |
| 手稿导入与投稿模板 | 可用 | PDF/DOCX/MD/TXT/TeX 两阶段导入；扫描 PDF 可显式启用本地 Tesseract + pypdfium2/pdftoppm，仅补无文字层页面并限制页数/语言包/逐页超时；DOCX 有界 OOXML 提取保留表格行、常见公式线性文本、脚注/尾注及结构审计；11 个原创结构模板；用户授权 venue ZIP 安全导入 | 当前机器无 Tesseract/渲染器，真实 OCR 尚未 live 验收；复杂 Word 公式保留为线性可编辑文本并要求人工复核版式；内置结构不是官方 venue 排版；尚未逐 venue 人工验收 |
| 项目 AI 助手 | 可用 | 右侧项目感知聊天；DB v6 持久化工作台/项目线程、消息及归档来源映射；版本化 JSON 和 `.json.gz` 受限桌面归档；preview/confirm 原子导入按来源 ID+完整内容指纹幂等，删除后可恢复，来源变化导入新副本；保留期批删与消息级不可逆敏感内容清理均用防并发 preview token，原文只留 SHA-256/大小/时间/原因审计 | 新回答依赖已配置 LLM；没有自动 push、无人确认写入、任意文件读取或静默自动清理；归档/脱敏是用户显式治理，不替代工作台备份 |
| 提示词模板 | 可用 | 工作台/项目作用域 CRUD、搜索、复制粘贴、`{{variable}}` 填充、插入 AI 对话；由 SQLite v4 迁移引入 | 无导入/导出模板包和版本历史；项目删除会级联删除项目模板 |
| 导出 | 部分可用 | Markdown、DOCX、LaTeX project、cited-only BibTeX、协作 bundle ZIP、Overleaf ZIP 均有 UI→磁盘结构/内容→重启历史 E2E；LaTeX 引用生成真实 `\cite{}` 并配套 references.bib | PDF 需本机 TeX；Pandoc 可选；PDF、真实 Overleaf Git 与目标 venue/Word/Overleaf 排版仍需环境或人工验收 |
| Skill | 可用 | 内置/用户/项目三级目录，优先级覆盖、CRUD、预览、推荐、LLM 草稿 | 无沙箱；Skill 是 prompt 文本，不应被视为可执行插件 |
| 版本 | 可用 | 数据库快照、项目级本地 Git、时间线、比较、恢复；默认 commit/branch/diff/restore 全部离线且永不自动 push；根源码已采用 MIT License、`main` 分支和 GitHub CI，并发布至 `LinJohn8/PaperCreator` | 首次公开提交前没有可恢复的源码历史；真实 GitHub/GitLab/SSH/Credential Manager 项目协作认证、内置分叉解决/自动 merge 和 detached HEAD 仍未验收；产品刻意不自动合并论文 |
| 配置与日志 | 可用 | default < settings < secrets < `.env` < process env 的确定优先级；稀疏环境覆盖、空值清除、API/Settings 页面无值来源诊断、脱敏；`ui.quick_start_version` 在工作台设置中持久化；后端双日志 + `desktop.log`，Help 与故障页均可真实打开日志目录 | `secrets.json` 不是系统凭据保险库，只是本地文件权限保护 |
| 测试 | 部分可用 | 当前 360 collected；非联网 `357 passed, 3 deselected`；DB v6、OCR、逐章节合并、复杂 DOCX、CLI 与 OpenAPI 157/181 路由指纹均通过；TypeScript/95-module 生产构建通过；真实 Electron 长链 `1 passed (1.6m)` 新增首启/Help/命令面板/日志/偏好/焦点与 1365×900、1100×700 截图验收，并保持归档、合并、导出和四次重启全链 | 3 个 live 检索测试未在本轮重复；主链仍需拆成可定位短场景；无 React 单元测试、clean VM、真实 OCR 引擎、专家金集、10GB 导入和真实远端认证验收 |
| Docker/Linux 部署 | 尚未实现 | FastAPI 代码本身可跨平台 | 仓库无 Dockerfile/Compose/服务单元；桌面目标当前是 Windows |

## 2026-07-28 本轮增量验证

```text
.\.venv\Scripts\python.exe -m pytest backend/tests -q -m "not live"
→ 357 passed, 3 deselected in 28.76s（360 collected）

npm run typecheck
→ Renderer 和 E2E TypeScript 均通过

npm run build
→ 95 modules transformed；生产构建成功

npm run test:e2e
→ 1 passed in 1.6m；真实 Electron 新增快速开始首启/重开/持久化/尺寸截图、菜单新建、日志 IPC 和焦点合同，并继续验证归档/脱敏、翻译、逐章节合并、LaTeX 引用、重启/退出/WAL

当前源码 OpenAPI
→ 14 个业务 router / 157 paths / 181 operations；method/path route snapshot 通过
```

本轮没有执行 `npm run package` 或 installer smoke，因此旧安装器仍只代表此前发行代码。桌面 E2E 已按单语言、创建后直接进入手稿和恢复最后项目的新契约完成回归。失败关闭超时后只对该测试拥有的 Electron PID tree 强制兜底；`npm run cleanup:e2e` 默认 dry-run，只扫描系统 Temp 下严格前缀且达到年龄阈值的隔离目录，`--apply` 才删除，绝不触碰用户工作台。

## 2026-07-28 既有发行链验证证据

```text
npm run test:backend -- -q
→ 313 passed, 3 deselected in 25.76s（316 collected）

npm run test:backend -- --live -q
→ 315 passed, 1 failed in 166.55s；唯一失败为 arXiv 公共服务 timeout
  → 单独重试被 arXiv 明确 rate limited；产品分别返回 timeout/rate_limited 诊断
  → 6 个离线 remote-Git 合同 + API pull/remove recovery 合同均通过

npm run test:e2e
→ 1 passed（总计约 1.2m，含标题栏视觉取证）；真实 Electron + 随机端口 Python 后端
  → 临时工作台 → Idea → 项目 → 本地 BibTeX 12 篇托管导入
  → 原生目录 IPC 选择 65-file 代码源 → resource_import Job/SSE → 原子托管副本
  → `.env`/node_modules 排除、audit metadata、ready 资源和 0 个 `.partial-res_*` 残留
  → Search 页重新探测 Local Provider → 选择 local + OpenAlex → Idea 规则展开后台检索
  → 本机 OpenAlex-compatible 服务连续 4 次 429 → Local 结果保留，UI 显示 outcome/HTTP/retry-after/建议
  → “仅重试 1 个可恢复来源”只提交 openalex、强制 use_cache=false → 恢复成功
  → 从当前项目文献库选择已有论文 → 自动填标题/摘要 → 第三次后台检索
  → 三条历史保存部分失败、恢复和 paper 执行的 request/stats；重启后仍恢复；mock LLM 调用为 0
  → Hashing+PCA Three.js 图谱 → Idea 精确投影 → 移除/重加（12→13→12→13）
  → CodeMirror 保存 → 编号章节文件
  → 本地 OpenAI-compatible HTTP → Reader/Synthesiser JSON → Writer/Translator SSE
  → Skill 注入 → prompt/output/token/4 steps 审计 → 英中双语章节写盘
  → 终态 Run 自动质量门禁显示真实引用键/字数/引用/检查证据与能力边界
  → 读取 Run 内嵌冻结正文与哈希 → 全屏盲评 → blind JSON 写入项目 exports/reviews/
  → Rubric v3：逐节核对修改章节、逐篇打开/核对引用来源；warn 需显式确认
  → accepted 还必须是 done + 自动 gate pass/warn + reviewer + 至少 20 字证据 notes + 六维均 ≥3
  → 评审同时绑定自动报告和 manuscript SHA-256；两名不同 reviewer 产生一致性统计
  → blind/identified 追加历史、指纹和汇总在无模型重启后仍恢复
  → 第二次 section run 的 Writer 首个 SSE delta 后 EOF → stream_interrupted → Step/Run/Job failed
  → partial output/双 snapshot/重试与恢复按钮 → 上一版完整手稿不损坏 → Electron 重启后诊断仍在
  → 外部文件修改 → 普通保存 409/diverged → 数据库优先并保留 disk backup
  → 再次外部修改 → 文件优先并建立 DB snapshot → CodeMirror 正确清除已持久化 dirty
  → VS Code 式单行标题栏：图标在 File 左侧、菜单桥、右侧动作与隐藏原生第二行菜单
  → UI 手动启用项目本地 Git → 同名 snapshot + commit；默认不联网/不显示可用 Push
  → 显式展开远端层 → 配置中文/空格本地 bare remote → 首次 Push → 协作者 clone 提交
  → Fetch 只更新 behind 状态且不改手稿 → ff-only Pull 前 snapshot/disk backup → DB/编辑器重载
  → UI 恢复基线并 commit → 再次 Push；无 force、分叉由后端合同拒绝
  → 协作者与本地从同一基线各提交 → Fetch 显示 ↑1/↓1/diverged → Pull 受控 409
  → 本地独有文件保留、远端独有文件未进入工作树；不启动自动 merge
  → UI 移除 remote → 本地 Git/branch 仍在，移除前后 HEAD 完全相同
  → 修改 tracked 手稿 + 新建 untracked 笔记 → confirm 展示精确范围
  → discard 前 DB snapshot + disk backup + binary patch → tracked 恢复、untracked 保留、DB in_sync
  → 创建探索分支 → 分支 commit → main↔分支↔main，正文/文件与 DB 随分支重载
  → 快照 → 修改/差异 → 确认回滚 → Markdown/DOCX/LaTeX/BibTeX/bundle/Overleaf ZIP 写盘
  → 后端重启/页面恢复 → Electron 重启
  → 13 篇 Library/图谱 seed、Idea/项目/正文/Agent run/快照/导出历史恢复

npm run build
→ tsc --noEmit 通过；Vite 92 modules transformed；生产构建成功

npm run setup -- --no-analysis --backend-only
→ 正确识别 Python 3.11；安装成功；papercreator --check: no problems found

npm run package
→ 可审计 SVG → 1024×1024 alpha PNG → electron-builder ICO；应用与安装器提取图标完全一致
→ PyInstaller onedir + NSIS 成功；安装器 135,381,021 bytes
  SHA-256 2A4C36AA4FFBF59316FF9DA91016C330D68BF8E7E500B3CE8062DF7E7B931E43
  冻结后端 608 files / 153,894,049 bytes
→ 最新冻结后端 `--check`：`no problems found`
→ 当前 OpenAPI：134 paths / 154 operations；Git remote 路径含 POST+DELETE，blind/analysis packet 路由仍存在

冻结后端专项
→ backend --check 通过；8 篇离线分析通过

npm run test:installer
→ 自动静默安装到系统临时中文/空格路径 → Playwright 启动真实已安装 PaperCreator.exe
  → isDev=false + bundled backend + Electron/FastAPI 同一工作台 + 7 类目录
  → API 创建 Idea/新论文项目 → 同路径静默覆盖 → 重启恢复 Idea/项目/marker
  → 静默卸载 → EXE/HKCU 卸载注册移除，.papercreator/DB/Idea/项目仍存在
  → 临时目录、安装注册和 PaperCreator/Electron/backend 进程无残留
```

本轮最初发现的 7 个测试失败已修复：健康接口导入遮蔽、静态转换路由顺序、线程池重启，以及测试/安装脚本的 Windows `shell` 参数问题。Electron E2E 又发现并修复了保存竞态、导入后项目计数不刷新、Agent prompt 审计为空、旧章节被重复持久化并误计为本轮输出、run 完成后历史行仍显示 running，以及重启测试把“项目列表已出现”误当成“项目已打开”的竞态。双向同步冲突 E2E 进一步发现：强制选边后已持久化文本仍会被误标 dirty；现在数据库优先只清除与重新加载 DB 完全相同的 dirty 值，CodeMirror 的外部 value reconciliation 使用专用 annotation，不再触发用户 onChange，同时保留请求期间的新输入。Git E2E 又修复了三个跨层合同：timeline 的摘要不能冒充完整 status、Git status 必须枚举实际 untracked 文件、手动 init 必须持久化 `git_enabled`，否则“保存版本”只建快照不提交。Agent 现在原子追加每次 LLM 调用的 SYSTEM/USER prompt，只写 `modified_section_keys`，并在完成后刷新 run history。研究链还纠正了 Hashing 被误当成语料相对嵌入的问题；固定 Hashing 可在完全离线环境中配合 PCA 精确投影新 Idea，且旧点不移动。

发布收口继续修复 Windows 生命周期：E2E 专用软件 GPU 隔离当前 Agent 会话的 Chromium `0xC0000135`，但不改变生产硬件加速；后端重启/退出以随机 capability 请求真实 Uvicorn graceful shutdown，等待 lifespan/WAL checkpoint/退出码 0，`taskkill` 仅为精确 PID tree 超时兜底；品牌渲染仍隔离 userData/session/cache。当前复核没有 PaperCreator/Electron/backend 进程。此前失败的开发 E2E 尝试可能在系统 Temp 留下仅含锁定测试 DB 的 `papercreator-e2e-*` 目录；它们不含用户工作台数据，当前执行环境拒绝越过文件操作策略强制删除，不能写成“0 临时目录残留”。

## 当前推荐方式

- 开发：Windows + Node.js 20+ + Python 3.10+；`npm run setup` 后 `npm run dev`。
- 仅后端：`npm run backend`，API 文档在 `http://127.0.0.1:8765/api/docs`。
- 开发使用 Windows + Node.js 20+ + Python 3.10+；终端安装包自身不依赖系统 Python/Node。
- 所有工作台实质数据在 `<所选文件夹>/.papercreator/`；停止应用后整体复制是推荐备份。

## 主要风险

1. 当前根目录缺少 `.git`，无法提供可靠修改历史、回滚或工作树差异。
2. 安装器已有正式图标但尚未签名，且缺少全新 Windows VM 验证；SmartScreen/杀软和系统依赖仍有发行风险。
3. Agent 已有本地确定性 OpenAI-compatible HTTP/JSON/SSE 正常与半途断流端到端证据，且四协议故障矩阵已有离线合同；但这只验证协议、编排、审计和恢复，不代表真实模型生成质量、实际成本或供应商长期 SLA。
4. 手稿同步已拒绝未确认覆盖并保留恢复副本，但基线粒度仍是整篇文档；两个不同章节分别被改也会保守地报冲突，必须显式选边，尚无自动三方合并。
5. 大目录导入的安全/取消合同已实现并通过源码 Electron 与冻结 HTTP 验证，但尚无真实 10GB/百万小文件、网络盘、超长路径、低空间卷和普通权限 junction 性能矩阵。
6. API 无认证；改变 `PC_HOST` 为非回环地址会扩大攻击面。

当前任务优先级见 [tasks/priority_tasks.md](tasks/priority_tasks.md)。

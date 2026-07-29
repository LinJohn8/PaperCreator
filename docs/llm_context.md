> 文档用途：后续 Codex/Claude Code 等代码 Agent 修改项目前的最小必读上下文  
> 最后检查：2026-07-28  
> 对应代码：整个仓库  
> 文档状态：基于当前代码和本轮验证整理

# LLM 开发上下文

## 项目身份

- 名称/版本：PaperCreator `0.1.0`。
- 目标：Windows 优先的本地多 Agent 论文工作台；检索 → 文献 → 3D 图谱/缺口 → 写作 → 双语/版本 → 导出。
- 形态：Electron + React Renderer + 本地 FastAPI；不是普通网站，也不是 Agent 实例 Wiki。
- 阶段：开发中。核心 Electron 链已贯通 quality v2→不可变双语正文→Rubric v3→blind packet→两位 reviewer/agreement→无模型重启，并继续覆盖研究、同步、Git、导出和 installer；但真实专家双盲金集、真实模型/费用、clean VM/签名和真实远端仍未验收。

## 当前事实

| 项目 | 位置/事实 |
|---|---|
| 桌面入口 | `apps/desktop/electron/main.cjs`；Renderer `src/main.tsx` / `App.tsx` |
| 后端入口 | `backend/papercreator/__main__.py`；ASGI `api/app.py::app` |
| API | 当前源码 14 个业务 router、157 路径/181 个 HTTP 操作；Swagger `/api/docs`；method/path SHA-256 route snapshot 防止静默删改；本轮未重打冻结后端 |
| 配置 | default → `settings.json` → `secrets.json` → `.env` → process env；环境为最终强制层；`GET /api/settings/sources` 无值诊断 |
| 工作台根 | 安装态用户选择普通文件夹；开发态默认仓库根；`PAPERCREATOR_WORKBENCH` |
| 数据根 | `<workbench>/.papercreator/`；AppData 只保留下次启动定位指针 |
| 项目/资料 | `projects/` 是新论文；`library/` 下 7 类输入资料，不可混为一个目录 |
| 数据库 | `papercreator.db`，DB schema v6（v3 双语目标；v4 提示词；v5 助手线程/消息；v6 归档来源映射）；workbench manifest 独立 schema v1；项目 manuscript sync manifest 当前 schema v2 |
| Skill | 内置 `resources/skills`、用户 `<home>/skills`、项目 `.papercreator/skills` |
| 日志 | `logs/papercreator.log`、`errors.log`、`desktop.log` |
| 打包 | SVG→PNG 品牌生成 → `scripts/build-backend.mjs` → PyInstaller onedir → electron-builder afterPack/rcedit → NSIS；安装态不依赖系统 Python，普通 Windows 权限不依赖 winCodeSign 符号链接解压 |
| 当前测试 | 360 collected；本轮非联网 357 passed/3 deselected（28.76s）；TypeScript/95-module 生产构建通过；真实 Electron 长链 `1 passed (1.6m)`，覆盖快速开始与原有助手/翻译/合并/四次重启链；当前源码 OpenAPI 157/181 |
| Git | 项目级封装存在；当前源码根没有 `.git` |

## 不可破坏的约束

1. 不读取私人内容，不删除或覆盖任何用户工作台 `.papercreator/`、projects、library、用户 Skill、项目 `.git`、数据库或未知运行文件。
2. 所有产品实质数据必须位于所选工作台唯一的 `.papercreator/` 下；AppData 不得成为第二数据根。
3. “新论文”是 `projects/` 中的写作项目；Idea/参考论文/我的论文/代码/数据集/补充材料/待分类是 `library/` 输入，分类语义不得混淆。
4. 导入默认复制托管副本；原路径只作 provenance。删除/移动前解析绝对路径并确认在允许的 projects/library 边界。四类目录必须保持既有原子合同：确定性 inventory/空间检查/link 与特殊节点隔离/源身份复核 → 同分类严格 `.partial-res_*` 分块复制 → 同盘 rename → DB 最后登记；取消/失败无 ready row 且清 staging，启动只回收严格保留名。不得改为直接写最终路径、跟随 reparse point 或先登记 ready。
5. 修改 SQLite/Pydantic/workbench/project 文件格式必须提供迁移、兼容读取、备份和回滚；DB v6、workbench manifest v1 与 manuscript sync manifest v2 是三个独立版本域，不可混淆。已发布 v1-v6 SQL 只能追加，不能原地修改；sync manifest v1 仍须保守兼容读取。
6. `Paper` 合并不能让 Provider 数据覆盖用户标签、备注、idea/own_paper 来源等字段。
7. Skill 加载失败、单 Provider 失败和可选分析依赖缺失不能使主服务整体崩溃。
8. 密钥不进入托管代码副本、日志、API 明文、文档、Git、错误消息或 subprocess 命令展示。
9. API 默认只绑定 `127.0.0.1`；在没有鉴权前不得默认暴露局域网/公网。
10. 手稿 DB→disk 与 disk→DB 是方向明确的操作；不得绕过 `.papercreator/manuscript-sync.json` 基线和 `ensure_sync_safe()`。schema v2 只在 DB/磁盘既有章节修改集合完全不重叠、无增删/重命名且 preview token 仍匹配时允许合并；同节双改、旧 v1 基线和结构变化必须让用户显式选边。所有合并/强制覆盖前保留 `.papercreator/conflicts/` 副本和 DB snapshot。
11. Agent 写入/故障/citation 合同保持不变。每个终态 Run 必须保存 quality report v2 与可重放 `review_manuscript`；Rubric v3 accepted 必须绑定 quality+manuscript 双 fingerprint，并在提交时重算逐节/整体完整性。不得只存 hash、从当前正文重建历史正文、回填旧 v1/v2 accepted，或让 blind packet 泄漏 run/project/model/pipeline/reviewer/既有决定/local path。human evaluation 只追加；agreement 只统计不同具名 reviewer，同一人重复评分不能算独立。自动/人工结论不是真值认证；无 LLM 不得隐藏审计。
12. 规划、实验性、live 未测与已验证功能必须分开表述；代码修改后同步 Wiki。
13. 分析增量定位不能移动已有点：只有 portable embedding 可对比新旧向量；Hashing portable、TF-IDF corpus-relative。`PositionResult.method` 必须贯穿 API/type/UI，精确投影与近邻插值不得混称。
14. `SearchRequest` 与 `ProviderStats` 是检索/恢复合同；mode/seed/providers/filter/cache/expansion 或 outcome/error/retry 字段修改必须同步 SearchBody、后台 Job、history/rerun、SSE、前端类型/诊断和 E2E。全失败必须先持久化再结束，不能丢诊断。
15. 项目 Git 默认只在 `<project>/.git` 本地 commit/branch，不得暗含联网或自动 push，也不得影响源码根/全局 identity。只有用户显式添加 remote 后才启用 Fetch/Pull/Push；不得 force push 或自动 merge。Fetch 只能改 tracking refs；Pull 必须要求当前分支、干净树、可快进，文件变化前先完成同步预检、DB snapshot 和磁盘手稿备份，成功后 reindex。移除 remote 必须保留 commits/branches/files/HEAD；分叉必须返回冲突并保留两侧历史。
16. `POST /api/assistant/chat` 是只读建议接口。回答不得被当成已执行操作；写手稿、保存 Skill、创建本地 commit 都必须由 Renderer 展示动作并由用户确认。助手不得自动访问 remote 或 push。
17. 内置论文模板是原创“内容结构”，不是官方 SCI/SSCI 或 Overleaf Gallery 排版文件。第三方 venue ZIP 必须由用户提供、确认授权，经路径/链接/加密/大小/SHA-256 检查后才能写入项目。DOCX 公式提取只保证有界线性文本和结构审计，不得声称恢复原 Word 排版。
18. MyMemory 是公共第三方翻译：必须让用户看到文本离开本机的隐私边界。LLM 翻译有费用；离线术语表不应冒充全文翻译。批量覆盖既有对照文本必须显式确认。
19. 手稿导入必须保持 preview→apply 两阶段合同；apply 复核 SHA-256，replace 需双重确认和恢复 snapshot，源副本只可写入当前项目 `.papercreator/imports/`。OCR 必须显式启用、只在本机运行、限制页数/语言包/逐页超时，且只补无文字层页面，不修改原 PDF。
20. 助手归档只允许受限 IPC 选择的 JSON/JSON.GZ，压缩前后均有 256 MiB 上限；Renderer 不得获得任意文件读取。导入必须 preview→confirm、来源 ID+内容 fingerprint 幂等并生成新本地 ID。消息敏感内容清理是不可逆替换，actions/meta 也必须清除，只可保留 hash/大小/时间/原因审计。

## 修改前检查

- `git status`：当前会提示无仓库；如果后续初始化 Git，先保护现有文件再提交。
- 目标模块在 [module_map.md](module_map.md) 的上下游与风险。
- API：后端路由顺序、OpenAPI、`api/endpoints.ts`、`api/types.ts` 和调用视图。
- 数据：DB schema、序列化、项目磁盘镜像、备份和迁移。
- 并发：JobManager、SQLite thread-local、SSE、取消检查和 React 状态竞态。
- 外部服务：限流、超时、重试、缓存、费用、可替换与无 key 降级。
- 打包：开发态 Python 与安装态 bundled exe 路径不同；冻结态不得读取源码 `.env`，extraResources/ASAR/后端 runtime 必须一起验。
- Electron 进程：`PC_E2E=1` 的软件/进程内 GPU 只用于自动化隔离，不能扩展成生产默认禁用硬件加速。Windows venv 可能是 launcher→真实 Python 双进程，普通退出/重启必须使用 Electron 每次启动随机生成且不暴露给 Renderer 的 `PC_DESKTOP_SHUTDOWN_TOKEN` 调用隐藏回环关机路由，等待 Uvicorn lifespan、WAL checkpoint 与 child 退出；`taskkill /T /F` 只可作为超时兜底。品牌渲染必须继续使用独立临时 userData/session/cache，不能污染或依赖用户 AppData。
- 测试：至少运行目标模块测试、`npm run test:backend`、`npm run typecheck`、`npm run build`；前端/Electron/IPC/持久化变化还要运行 `npm run test:e2e`。

## 当前 P0/P1/P2

- P0：没有已确认会立即丢数据的开放缺陷；但任何路径防护回归按 P0 处理。
- P1：clean Windows VM/代码签名；真实 LLM 全自动/分段写作质量与费用验收；真实 GitHub/GitLab 认证和分叉解决；根 Git 基线（需用户确认）。学术检索与 LLM 确定性故障 UI/恢复、Remote Git 本地安全闭环与六类无外部依赖导出 E2E 已完成，真实模型/逐源 live/PDF/真实 Overleaf Git 仍属专项环境验收。
- P2：目录导入真实 10GB/百万小文件/网络盘/长路径/低空间/junction 矩阵、live Provider 稳定性、真实论文格式/本地 OCR 人工验收、图谱大样本性能和可解释 UI。同节冲突的三方内容合并与 CRDT 仍未实现；不同章节的安全合并已完成。

详情与验收标准见 [tasks/priority_tasks.md](tasks/priority_tasks.md)。

## 本轮重要变更

- 工作台现在恢复界面语言与最后项目；第二实例可打开检索或选择新工作台。界面按中文/英文单选显示，不再把两种语言拼在同一标签；检索源切换提交真实 `enabled_providers`，空 PATCH 的 `no settings were provided` 已修复；普通通知缩短，错误保留到手动关闭。
- 写作系统升至 DB v4：v3 增加 `sections.target_words_zh`；v4 增加工作台/项目提示词模板。章节支持完整 CRUD、双目标、双语进度、翻译、文章导入导出和 venue ZIP；多格式提取全文 sidecar 位于工作台 cache，手稿导入审计副本位于项目 `.papercreator/imports/`。
- 项目 AI 助手保持只读建议与二次确认动作；DB v6 独立持久化工作台/项目线程、有序消息和归档来源映射。支持 JSON/JSON.GZ 导出、preview/confirm 幂等导入、默认关闭保留期、preview-token 批量删除及消息级不可逆敏感内容清理；无模型重启仍可读。历史动作只恢复为建议，不会自行写入或执行。
- 手稿同步 manifest 升至 v2，保存逐节 filename/fingerprint；不同章节的 DB/磁盘修改可在预览状态未变化时安全合并，写前保存 DB snapshot 和两侧镜像。同节双改、增删/重命名和旧 v1 基线继续阻塞。
- 新增可选本地扫描 PDF OCR（Tesseract + pypdfium2/pdftoppm）与真实能力探测；当前机器缺引擎，仅 mock 合同通过。DOCX OOXML 提取现在有 XML part 上限，并保留表格行、常见 OMML 公式线性文本、脚注/尾注和结构元数据，exact golden 已通过。
- CLI `--host/--port` 现在贯穿 app factory、lifespan 日志、CORS 与开发热重载；新增路由快照和默认 dry-run 的严格 Temp E2E 清理命令。
- MyMemory 只有用户选中并确认公共外发后才联网；短文同步，长文/批量走≤100k 字符/250 请求的 cancellable Job。Worker 只产出 durable 完整预览，apply 校验双侧 SHA-256、建快照并一次写入，失败/取消不留下半完成手稿。
- 新增 11 个原创结构模板；官方/第三方排版文件通过用户提供并确认授权的 ZIP 导入，不能把两者混称。MyMemory 翻译是公共服务，LLM 翻译按已配置 Provider 计费。
- 修复 Windows 桌面退出/重启：私有随机 capability 只在 Electron→后端子进程环境和关机请求头中存在；Renderer/OpenAPI 不暴露。退出先请求 Renderer 保存全部 dirty 章节，失败则取消退出；成功后关闭 SSE/polling，再等待 Uvicorn lifespan checkpoint/退出码 0。真实 E2E 已验证未手动保存的正文经退出和第四次启动恢复。
- 完成安全大目录资源导入：四类目录使用可取消 `resource_import` Job，具备 inventory、空间预检、link/reparse/special 排除、代码 secret/依赖排除、TOCTOU 复核、4 MiB 分块摘要、同盘 staging/原子 rename、DB 最后登记和严格启动回收。桌面有进度/取消/后台 Jobs，`waitForJob` 用 SSE + durable polling 防丢完成事件；Electron 实际导入 65 个代码文件并验证审计和 0 staging 残留。
- 完成 LLM/Agent 外部故障恢复闭环：四协议统一 outcome/error/retry/HTTP/hint，严格 terminal event，所有非流/流/embedding/JSON parse 失败进入 usage；断流 partial output 写失败 step，Step/Run/Job/SSE 诊断一致，双 snapshot 与原 request 支持显式 retry/restore。Electron 已真实验证 Writer 首 delta 后 EOF、上一版手稿不损坏和重启后审计恢复。
- 完成不可变评审证据：quality v2、全稿双语快照、逐节/摘要/整体 SHA-256、Rubric v3 双指纹与篡改/stale 拒绝；blind/analysis packet、项目内导出、双评 agreement 和 Electron 重启闭环。旧 v1/v2 保持兼容。仍不等于真实专家金集或绝对事实验收。
- 新增 PaperCreator 可审计 SVG/PNG 品牌资产及普通权限可复现打包钩子；应用与 NSIS 提取图标一致，EXE metadata 正确。不得重新启用 electron-builder `signAndEditExecutable`，否则无 Developer Mode 的 Windows 可能再次因 winCodeSign 内 macOS symlink 解压失败。
- 新增 `scripts/installer-smoke.mjs` 与 `test:installer`/`test:release`：拒绝覆盖已有用户安装，在受控中文/空格临时路径自动验证最终安装包、生产 EXE/bundled backend、七类工作台、Idea/项目、同路径升级恢复及卸载保留 `.papercreator`；NSIS 注册清理由有界轮询等待。
- 完成 Remote Git 桌面安全闭环：URL 空格解析与凭据脱敏、命名 remote、Fetch、非强推 Push、clean+ff-only Pull；Pull 写前 snapshot/磁盘备份、写后 DB reindex，分叉/脏树/non-fast-forward 不改本地文件。真实 Electron 用中文/空格 bare remote 和协作者 clone 验证完整 UI；真实认证与分叉解决仍未验收。
- 修复 Search 正式后台任务曾丢失 `use_llm_expansion`、历史 rerun 无法复现选择的问题；将开关纳入 `SearchRequest` 持久化合同，并增加两项 API 回归。
- Search 页刷新动态 Provider，可从文献库选择已有论文；结构化展示每源 outcome/HTTP/retry-after/中英建议。真实 Electron E2E 已验证 Local+OpenAlex 429 时保留好结果、失败历史、只重试可恢复的 OpenAlex、强制关 cache、恢复成功，以及三条执行历史重启恢复。
- 实现手稿 DB/磁盘双摘要基线、409 冲突保护、Editor 选边 UI、被覆盖侧恢复副本，以及 Git discard 的 snapshot/手稿/binary patch/reindex 闭环。
- 给 Git checkout/restore、snapshot restore、Agent persist 和 Overleaf apply 增加首次修改前的同步保护与回归测试。
- 完成 Versions 桌面的 Git init/commit/discard/探索分支/checkout 闭环；修复 timeline/status 合同、nested untracked 展示和手动 init 未持久启用项目的问题；真实 E2E 证明恢复材料、untracked 保留和分支 DB/磁盘一致。
- 建立并扩展 Playwright Electron 真实 E2E，覆盖临时工作台、检索/图谱/Idea、CodeMirror/Agent/Skill/双语、同步冲突、Git/快照、六类无外部依赖导出和重启恢复；新增导出审计修复了 citation key fixture 大小写错误及 LaTeX `\cite` 被二次转义的问题。
- 纠正 Hashing 的算法身份：固定 Hashing 是弱词法但 portable 的离线 embedding，只有 TF-IDF 是 corpus-relative；新增 exact/interpolated 定位方法合同、前端可审计标签和打包后端能力验证。

- 修复 Windows Node 子进程 `shell:true` 导致 Python 探测和 pytest `-m "not live"` 参数破坏。
- 修复 `/api/system/health` 导入名遮蔽。
- 将 `/api/export/convert` 静态路由注册到动态 `/{project_id}` 前。
- 使 JobManager 在 lifespan shutdown 后可在同一进程重建线程池。
- 实现 `.papercreator` 单根工作台、7 类资源托管导入、DB schema v2 和工作台首页。
- 集成 PyInstaller/NSIS；完成本机安装、覆盖升级、重启恢复和卸载保留数据验证。
- 建立本 Wiki。详见 [changelog_internal.md](changelog_internal.md)。

## 文档同步清单

每次完成代码修改后检查：`current_status.md`、`changelog_internal.md`、对应 `systems/` 与 `flows/`、`api_reference.md`/`configuration.md`（若合同变化）、`tasks/completed_tasks.md`、`pending_tasks.md`、`priority_tasks.md`、`known_issues.md` 和本文件。

> 文档用途：全部重要待办的背景、风险、实现建议和验收  
> 最后检查：2026-07-28

# 待完成任务

## Windows 正式发行验收

- 当前状态：部分可用
- 背景：PyInstaller+NSIS 与本机安装/覆盖升级/卸载保留数据已通过，但未覆盖全新系统和签名。
- 目标：无 Python/Node 的干净 Win10/11 x64 可安装运行，SmartScreen/杀软/路径场景可解释。
- 已完成部分：standalone backend、builder 集成、工作台选择、正式 icon/metadata；本机自动 installer E2E 覆盖中文/空格路径、真实已安装应用、卸载保留数据和同路径覆盖升级。
- 未完成部分：clean VM 矩阵、标准用户/杀软验收、代码签名、许可证/SBOM 审计。
- 涉及文件：`apps/desktop/package.json`、`electron/main.cjs`、packaging scripts、未来 CI/资产。
- 上游依赖：签名证书、正式品牌资产、支持的 Windows/CPU 范围。
- 下游影响：所有终端用户交付。
- 风险：体积、杀软、模型包、路径空格、升级覆盖、许可证。
- 建议实现方式：对当前 onedir 产物做 Win10/11 VM、中文/空格路径、标准用户、离线、杀软、覆盖升级和卸载矩阵；再签名。
- 验收标准：新 Win10/11 x64 无开发环境，安装→选择工作台→启动→分类导入→健康→免费检索→离线图谱→编辑→导出→升级保留数据→卸载不误删 `.papercreator`。

## 目录导入真实规模与 Windows 文件系统矩阵

- 当前状态：部分可用
- 背景：安全 Job、进度/取消、空间预检、link/reparse 排除、TOCTOU 校验、同盘 staging、原子 rename、DB 最后登记和启动残留回收均已实现并通过受控测试；但当前证据是 65-file Electron fixture 和小型后端临时目录，不等于真实极限规模。
- 目标：量化大目录在本地盘、网络盘、长路径、低空间和大量小文件下的吞吐、事件/DB 压力、取消延迟与恢复行为。
- 已完成部分：`resource_import` Job/SSE；4 MiB 分块；确定性 inventory；安全余量；源 size/mtime/device/inode/resolve 前后复核；空目录复核；严格 staging 清理；嵌套 link audit；Electron 原生目录 UI。
- 未完成部分：真实 10GB、百万小文件、UNC/映射网络盘、Windows 超长路径、真实低空间卷、普通用户权限下真实 junction/reparse point、杀软占用 staging、异常断电/进程终止的矩阵尚未验证。
- 涉及文件：`store/resources.py`、`api/routes/workbench.py`、`api/app.py`、`WorkbenchPanel.tsx`、`api/events.ts`、benchmarks/E2E fixtures。
- 上游依赖：可销毁的测试卷/网络共享、受控 junction fixture、性能记录方案。
- 下游影响：大型代码库/数据集的可预测性与 Windows 发行信心。
- 风险：大量逐文件 progress 造成 Job DB/SSE 写放大；网络文件身份语义和本地 NTFS 不同；长路径/杀软占用可能暴露清理边界。
- 建议实现方式：建立不会触碰用户数据的生成式 fixture 和独立卷矩阵；记录 scan/copy/commit 时长、峰值内存、DB/SSE 更新数、取消延迟和残留；保留现有失败不登记/不跟随 link 的安全合同。
- 验收标准：上述矩阵均有可复现报告；10GB 与百万文件任务可观测、可取消；低空间和源变化不产生 ready row；异常重启只回收严格 reserved staging；不泄漏 `.env*`、不跟随 link/reparse；性能阈值和支持边界写入发布文档。

## 真实 LLM Agent 质量与失败矩阵

- 当前状态：实验性
- 背景：确定性本地 OpenAI-compatible HTTP/JSON/SSE 已证明 Electron→后端→Agent→Skill→审计→双语写盘→快照→质量报告/人工评审→重启链，但没有真实云 key/本地生成模型的论文质量验收。
- 目标：验证 full_auto/section/stitch 的质量、引用、成本、取消和恢复。
- 已完成部分：4 协议与故障链；稳定 citation registry；quality v2；可重放全稿/逐节/摘要 hash；Rubric v3 双 fingerprint、篡改/stale 拒绝；blind/analysis packet 与项目内导出；不同具名 reviewer 的 kappa/MAD/within-one 统计；桌面双评和无模型重启 E2E；旧 v1/v2 兼容。
- 未完成部分：选定公开固定论文集、专家预注册 gold rubric、实际招募至少两名独立专家并随机/封存/揭盲、自动 gate 阈值校准、真实云/本地模型质量与计费、多供应商低频 live、长文和高并发测试。已有一致性“算法和 E2E fixture”，不等于已有真实专家一致性结论。
- 涉及文件：`llm/`、`agents/`、tests/fixtures、docs。
- 上游依赖：用户选择可用 Provider/key 或本地模型。
- 下游影响：核心写作承诺。
- 风险：费用、数据外发、幻觉、引用错配、模型版本漂移。
- 建议实现方式：先 10–20 篇公开 corpus 和单章节，冻结可打开的来源证据，至少两名 reviewer 独立评分并记录分歧；记录 model/version/config/token/cost；用人工结果校准 warn/fail 信号，只对真实 Provider 做受控低频抽样，再扩展全稿；只存公开或脱敏 fixture。
- 验收标准：每 pipeline 可恢复；引用键/metadata 一致；所有 accepted 满足 v3 且可重放精确正文/来源；真实样本至少双评并报告 kappa/MAD/within-one、自动信号误报/漏报和置信区间；不超过预算；人工 accepted 不得定义为绝对事实真值。

## 前端与 Electron E2E

- 当前状态：部分可用
- 背景：TypeScript/build 不能验证交互和 IPC。
- 目标：覆盖核心 happy path 与断线/破坏性保护。
- 已完成部分：真实 Electron/Python 核心链；检索/图谱/Idea；Agent/双语/断流；质量评审；同步/Git/Remote Git；六类导出；installer；单语言 accessible name、创建后直接进入 Editor 的新契约已纳入长链修复。
- 未完成部分：把 locale 重启、检索源保存、章节 CRUD/双目标、手稿多格式导入、离线/公共翻译、提示词 CRUD、助手/Skill/本地 commit 确认拆成独立短场景；另有逐个公网 Provider、真实模型、真实远端认证、PDF/Overleaf、首次 dialog、clean-VM、CI/组件测试。
- 涉及文件：`apps/desktop/e2e/`、`apps/desktop/src/`、`electron/`、package scripts。
- 上游依赖：稳定打包/启动方式。
- 下游影响：发布信心。
- 风险：Three.js/CodeMirror/原生 dialog 难自动化。
- 建议实现方式：沿用真实后端和临时 workbench；外部边界采用本机协议兼容服务做确定性层、低频真实服务做 live 层；图谱验证数据和语义标签而非像素；保持单 worker 防止端口/进程争用。
- 验收标准：已满足的导入→Local+OpenAlex 失败/恢复→Idea/已有论文检索→history/restart→analysis→idea place→editor/save→Agent 正常/断流恢复→冲突恢复→Git→六类导出→重启链、installer 与安全合同保持稳定；再加入真实模型质量/live 层、真实远端认证/分叉、PDF/Overleaf 专项环境与 clean-VM。

## Live 检索合同和大库基准

- 当前状态：部分可用
- 背景：服务/限流/字段持续变化；2026-07-28 最新单次 live 中 arXiv 先 timeout、单独重试后 rate-limited，且尚无持续监控。
- 目标：识别代码回归和外部波动，量化 1k/10k 文献性能。
- 已完成部分：3 live tests、结构化 per-source stats、cache/rate limit、离线 parser/HTTP/异常 fixtures、全失败历史、Electron 429 部分失败与仅重试故障源；2026-07-28 最新显式 live 为 315 passed/1 arXiv timeout，单独重试确认 rate-limit，产品诊断符合合同但公网 SLA 不能记为全绿。
- 未完成部分：定期受控执行、跨时间趋势/字段漂移报告、1k/10k 性能报告。
- 涉及文件：`tests/smoke_*`、providers、CI/benchmarks。
- 上游依赖：网络/contact email/可选 key。
- 下游影响：检索可靠性和图谱规模。
- 风险：公共 API 负担、flaky test。
- 建议实现方式：手动/nightly 低频，不阻塞普通 PR；保存 schema 摘要而非私人结果。
- 验收标准：默认 3 源有至少 2 源成功；429/timeout 可解释；无违反限流；基准含内存/时长/DB size。

## 根仓库 Git/CI/治理

- 当前状态：已完成基础治理
- 背景：2026-07-29 之前没有 `.git`、LICENSE 或 CI，且无法追溯更早历史。
- 目标：保护源码和自动验证。
- 已完成部分：强化 `.gitignore`、MIT License、半成品/低维护 README、贡献与安全说明、`main`、GitHub remote、首次审计提交，以及 Windows CI 的离线后端/类型/构建/Wiki 检查。
- 未完成部分：按仓库所有者需要配置 branch protection；CI 不替代 clean VM、代码签名、真实 Provider 或 Electron 长链专项环境。
- 涉及文件：整个仓库。
- 上游依赖：用户已明确授权公开仓库；GitHub 账户策略仍由仓库所有者控制。
- 下游影响：协作、回滚、发布。
- 风险：后续贡献者误提交 `.env`/runtime data/大模型；首次提交前历史不可恢复。
- 建议实现方式：保持 PR/CI、密钥扫描和依赖更新；需要时启用 branch protection 与私密安全报告。
- 验收标准：ignored data 未跟踪；首次提交可复现；CI 全绿；文档记录策略。基础项已满足。

## 多格式提取、OCR 与翻译可靠性

- 当前状态：部分可用
- 背景：PDF/DOCX/MD/TXT/TeX 提取、离线术语、MyMemory 与 LLM 翻译已经接通，但扫描 PDF、复杂版面和长章节是明确边界。
- 目标：在不静默损坏正文、不意外外发文本的前提下覆盖常见论文格式和长文翻译。
- 已完成部分：2,000,000 字符提取上限、80,000 字预览、sidecar、OCR 告警、损坏/加密 PDF 告警；MyMemory/LLM Provider 明示；MyMemory 显式公共外发确认、≤100,000 字符/250 请求 Job、句段稳定分块、代码/展示公式本地保留、限速/Retry-After/timeout/network 重试、取消、durable 完整预览、双侧 SHA-256 陈旧拒绝、快照与一次性事务应用。
- 已完成新增部分：可选本地 Tesseract OCR adapter、渲染器/语言包能力探测、1–200 页与逐页超时边界、仅补无文字层页面；复杂 DOCX 表格行、常见 OMML 公式线性文本、脚注/尾注和 part 大小限制 exact golden。
- 未完成部分：当前机器没有 Tesseract/渲染器，真实中英扫描 corpus 尚未 live；复杂 Word/venue 最终版式仍需人工；MyMemory 运行中进程重启不续跑（按通用 Job 规则失败，已完成预览可恢复）；真实公共服务低频 SLA/条款趋势尚未建立。
- 涉及文件：`importers/document_text.py`、Writing translation routes、`EditorView.tsx`、公开无版权 fixtures。
- 上游依赖：OCR 引擎/语言包选择；公共服务条款。
- 下游影响：自有论文定位、手稿迁移和双语写作。
- 风险：公式/结构丢失、敏感未发表文本外发、重试重复覆盖。
- 建议实现方式：能力探测式本地 OCR；段落/句子感知块与稳定顺序；译文先预览后一次性确认写入；保留原文件和 extraction metadata。
- 验收标准：扫描中英 PDF 可选 OCR；复杂 fixture 的标题/正文/公式边界有 golden；20k+ 字章节可取消/续做且不重复；未选择公共 Provider 时零外网请求。

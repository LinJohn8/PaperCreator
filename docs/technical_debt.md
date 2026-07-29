> 文档用途：确认的工程债务，不等于立即重构授权  
> 最后检查：2026-07-28

# 技术债

| 债务 | 证据 | 风险 | 建议 |
|---|---|---|---|
| 大文件 | `embeddings.py` 942、`backends.py` 1069、`orchestrator.py` 896、`store.ts` 818、多个视图 600–1000 行 | 修改冲突/认知负担；四协议故障处理虽有合同测试但集中在一个 adapter 文件 | 先保持 16 个 LLM 故障合同，再把通用 HTTP/stream diagnostics 与各协议 parser 分层拆分 |
| 前端全局 Store 高耦合 | 项目/文献/分析/写作/Agent/UI 同文件 | 并发状态回归 | slice + query/cache 策略 |
| Python 最低依赖上界宽 | NumPy/sklearn 会解析未来版本 | 不可复现/序列化变化 | 锁定已验证组合或生成 lock |
| 前端测试/CI 覆盖不足 | Playwright Electron 仅 1 条串行真实链（已含检索 429/恢复、图谱/Idea、Agent 正常/断流恢复、冲突、Git、导出/重启）；另有 installer 与后端合同，但无 Vitest/RTL/CI | 真实模型/远端认证和非核心 UI/组件回归；单链失败定位成本高 | 拆分可重复场景，补组件测试并接入 CI |
| AssistantPanel/EditorView 继续变长 | 助手、提示词、Skill 草稿、Git 确认和文章导入/翻译弹窗集中在少数 TSX | 状态依赖与 UI 回归难定位 | 在补齐交互测试后按 dialog/hooks 边界拆分，不改变安全确认合同 |
| AI 对话治理仍缺 run prompt 清理联动 | DB v6 已有 JSON/JSON.GZ 幂等导入、保留期批删和消息级不可逆脱敏，但 Agent prompt/output 在 `agent_runs.result` 是另一数据域 | 用户清理聊天消息后，Run 审计中可能仍保留独立的相同项目文本 | 先明确跨数据域预览范围与不可破坏的质量证据，再增加按 run/时间/敏感级别导出清理；不能让聊天清理隐式破坏评审证据 |
| 翻译 Job 不跨进程续跑 | MyMemory 已有≤100k/250 请求稳定分块、限速重试/取消、durable 进度与完整预览原子应用 | 运行中退出会按通用 Job 规则标失败，已完成预览不受影响 | 若真实长文数据表明需要，再增加不含明文的块 checkpoint/cache 与明确“继续”入口；不得自动重发公共文本 |
| OCR/复杂格式只有确定性合同，无本机真实引擎证据 | 本地 Tesseract adapter、页数/语言包/超时边界和复杂 DOCX exact golden 已完成；当前机器没有 Tesseract/渲染器 | 不同扫描质量、语言包与复杂 Word 版式仍可能产生识别/线性化误差 | 在带中英语言包的隔离环境建立公开扫描 PDF live corpus；逐 venue/Word 人工核对，不把线性公式文本冒充原排版 |
| Search 合同跨层重复 | `SearchRequest/SearchResponse/ProviderStats`、`SearchBody`、SSE camelCase、前端类型、history JSON、rerun/定向恢复分别传字段；曾丢失 expansion 和故障语义 | 预览、后台 Job、历史、实时进度与恢复行为漂移 | 生成/共享 OpenAPI 类型；对每个请求字段和 outcome 补 async/history/SSE round-trip 合同 |
| Agent prompt 审计体积无分层保留策略 | 每次调用保存完整 SYSTEM/USER，Reader 同一步可追加多篇 | DB 增长、论文摘要/用户文本的本地敏感面 | 保留当前可审计默认，增加按 run/时间/敏感级别的预估、导出与清理策略 |
| 分析 backend 语义易漂移 | portable/corpus-relative 同时影响 cache、增量算法、capabilities 和 UI 文案；本轮曾发现 Hashing 元数据残留为 false | 同一算法在运行和能力接口中自相矛盾 | 用共享 backend descriptor 驱动判断与能力输出，并保留 Hashing/TF-IDF 合同测试 |
| 安装测试/发行信任不足 | PyInstaller+NSIS、正式图标、版本资源和本机自动 installer E2E 已工作，但无 clean VM/Authenticode | SmartScreen、杀软、系统依赖与洁净机风险 | VM matrix、签名、SBOM/许可证审计 |
| 同节冲突仍无内容级三方 merge | sync manifest v2 已按章节保存共同 fingerprint 并安全合并非重叠章节；同一章节双改继续阻塞 | 长章节由两侧独立编辑时仍需人工选边/外部 diff | 如真实需求成立，schema v3 保存可验证 base 内容或 patch，提供显式三方 diff；保持 snapshot/两侧镜像和不猜测默认 |
| API 文档表仍需手工同步 | 当前源码 157 paths / 181 operations 已有 method/path SHA-256 route snapshot | 快照能防静默路由漂移，但不能自动更新说明文字/前端类型 | 后续从 OpenAPI 生成客户端类型和 API 索引；CI 中保留当前 snapshot |
| Agent 质量历史和冻结全文嵌入 `agent_runs.result` JSON | 无迁移即可追加并重放精确正文，但每个 Run 增加约一份全稿，汇总需解析整列 JSON，无法索引 reviewer/维度/fingerprint | 大型金集的数据库膨胀、查询成本、复杂筛选与跨版本迁移 | 金集流程稳定后规范化 manuscript artifact/evaluation/review-target 表，可选内容压缩/去重；保留 v1/v2/v3 JSON 兼容和双 fingerprint 语义 |
| 自动质量门禁缺少真实金集校准 | 当前阈值主要是结构性规则（如修改章节达到目标字数 35%），模型辅助证据也不是 gold label | pass/warn 与真实研究质量相关性未知 | 建立领域分层真实论文/故障样本、人工双评与阈值校准；保持语义核验永不自动声明 |
| Skill 信任只有来源/预览 | prompt 注入风险 | 内容质量/泄露 | provenance/signature/policy |
| 无正式代码格式工具 | 无 Ruff/ESLint/Prettier 配置 | 风格和静态 bug | 分阶段引入，避免一次性大改 |
| 数据版本分散 | DB schema v6、workbench manifest v1、manuscript sync manifest v2、venue manifest 各自演进 | 升级风险 | 分层迁移 fixtures/manifest version；继续明确各版本域不能联动递增 |
| 运行时 polling 与 SSE 双机制 | jobs/log timer + SSE | 重复请求/状态竞态 | 明确 ownership/去重/可观测性 |
| 目录导入进度写放大且无断点续传 | 逐文件/分块 progress 会更新 durable Job 并广播 SSE；异常重启按严格名称清 staging 后整任务重来 | 百万小文件时 DB/SSE 压力与重试成本可能较高 | 先做真实规模基准并节流/合并 progress；若确有需求，再设计带 inventory 版本与摘要校验的安全断点续传，不能牺牲原子 ready 合同 |

`pass` 搜索结果主要是可选 import/异常清理，不代表占位逻辑。不要仅凭文本匹配大规模重构。

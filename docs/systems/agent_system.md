> 文档用途：多 Agent 角色、流水线、黑板、预算和写入保证  
> 最后检查：2026-07-28  
> 对应代码：`backend/papercreator/agents/`、`store/runs.py`  
> 文档状态：实验性

# Agent 系统

| 角色 | 输出/责任 |
|---|---|
| planner | 论文类型、贡献主张、章节策略 |
| reader | 逐文献结构化问题/方法/发现笔记 |
| synthesiser | 主题、共识、分歧 |
| ideator | 过滤算法缺口候选并定位用户 idea |
| outliner | 章节 brief、字数和文献分配 |
| writer | 逐章节草稿 |
| critic | 无证据主张、结构和遗漏问题 |
| reviser | 有针对性修订 |
| citation_checker | 引用键存在性和支撑关系 |
| translator | 配对语言章节 |
| polisher | 跨章节术语、重复和衔接 |

流水线：`full_auto` 全链路；`section` 只处理选中章节并复用已有大纲/笔记；`stitch` 连接独立章节；`custom` 按角色列表执行。

Blackboard 从 Project、项目全部 Papers、最近/指定 Analysis 和已有 sections 构建。`load_blackboard()` 先用完整项目文献建立 citation registry，再应用本轮 paper 子集，所以 full/section/retry 中同一论文的 author-year key 不会因选择范围改变；prompt 与导出共用 `writing.citations.CitationKeyMap`，不再各自维护碰撞规则。已有正文可作上下文，但 Writer/Reviser/Translator/Polisher 每次修改都登记 `modified_section_keys`，`_persist()` 只按 outline 顺序写这些章节，并从最终 primary text 的 `[KEY]` marker 重新解析 `cited_paper_ids`；Reviser/Polisher 改掉引用后不会留下陈旧元数据。每步写 `agent_steps`，运行写 `agent_runs` 和 `llm_usage`；`Agent.execute()` 绑定 active step，`ask()`/`ask_streaming()` 在调用前把完整 SYSTEM/USER prompt 原子追加到该 step，同一步并发精读多篇论文不会互相覆盖。prompt 可按维护策略清理。开始/结束各建 snapshot，章节完成即持久化，预算、取消或单章节失败保留已完成结果。

LLM/协议失败会把相同结构化 diagnosis 写入 step `meta.failure`、run `result.failure/failures`、Job `result.failure` 和 SSE。流式中断的已收文本写入失败 step `output`，不会写成完整章节；Run 记录 `recovery.strategy=partial_work_preserved`、前后 snapshot、恢复用 pre-run snapshot 和可重试标志。独立章节可继续尝试，但任一未恢复的 LLM step 会让最终 Run 与 Job 都是 `failed`，不再出现 Run 失败而 Job/桌面 toast 显示完成。失败后桌面先重新加载 run/document/timeline，再提供“重试相同运行”“比较或恢复快照”和按需打开模型设置。

取消为协作式，流式步骤也必须检查并把 step 标为 `cancelled`；预算按 durable usage ledger 计算，包含失败调用、JSON 修复重试和断流 token。引用使用稳定 `[KEY]` 标记；Agent 输出不能直接成为“事实已核验”的证据。

## 质量门禁与人工验收

orchestrator 在每个终态构建 quality report v2 和 `review_manuscript`。前者保存结构检查、逐节 primary/paired hash 与冻结来源摘要；后者保存可重放的完整双语正文、逐节 hash/字符数、modified 标志、post-agent snapshot id 和整体 fingerprint。hash 证明字节一致性，内嵌正文保证项目后续修改后仍能阅读当时版本；二者缺一都不能形成 v3 accepted。

Run detail 展示 gate、字数、引用论文/marker/无效键指标、逐项证据和明确能力边界。quality report 的 citation registry 附带每篇实际引用论文的 key、题名、年份、DOI、URL、PDF path 和摘要可用性，桌面要求逐篇核对；本轮每个 modified section 也必须逐节确认。人工 Rubric 有事实依据、引用支撑、方法合理性、文献覆盖、论证连贯、写作清晰六项 1–5 分；评审记录只追加。

Rubric v3 的 `accepted` 是服务端强制合同：除原有 done/pass-warn/逐节逐篇/来源/warn/六维≥3/reviewer/notes 条件外，必须重算冻结正文全部 hash、质量报告逐节证据与 manuscript fingerprint，且客户端提交它实际阅读的同一 fingerprint。篡改、缺失与 stale form 都被拒绝。revision/rejected 仍可用于终态故障或 legacy Run。

每条 v3 review target 同时绑定自动报告与 manuscript fingerprint。旧 v1/v2 记录原样兼容，不回填冻结正文或伪升级 accepted。评审、报告与正文快照仍保存在 `agent_runs.result` JSON，IMMEDIATE transaction 防止并发追加丢记录；这避免当前 DB migration，但也是待金集规模稳定后需要规范化的技术债。

盲评视图以全屏证据面板隐藏外层 run/model/pipeline 信息，blind packet 进一步从 JSON 删除 run/project/model/pipeline/reviewer、既有人工结论与本地 PDF path；analysis packet 才恢复 provenance、模型/角色、tokens/cost 和全部评审。两种包都带 packet fingerprint，并只能原子写到项目 `exports/reviews/`。这提供双盲实施基础，但当前没有账号/任务分配服务，专家随机化仍需外部流程。

汇总 schema v2 继续区分 latest 与 append-only，并增加只统计不同具名评审人的 exact agreement、decision kappa、六维 MAD/within-one/quadratic weighted kappa；同一 reviewer 重复提交不算独立复评。

后端测试覆盖 fingerprint 稳定/正文变化、legacy compatibility、快照篡改与 stale 拒绝、blind non-disclosure、受控导出路径和 agreement 数学合同。Electron E2E 真实阅读冻结正文、导出 blind packet、以 blind/identified 两位 reviewer 追加接受结论、计算一致性并在无模型重启后恢复。它证明基础设施，不代表真实专家已完成金集评审。

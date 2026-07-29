> 文档用途：Agent run 从提交到写盘的顺序与保证  
> 最后检查：2026-07-28  
> 对应代码：`api/routes/agents.py`、`agents/orchestrator.py`

# Agent 运行流程

验证项目/pipeline/LLM/roles → 创建 agent_run 和后台 job（request 保存可重跑的 paper/skill/role/config 参数）→ load Blackboard（先用完整项目 Papers 建稳定 citation registry，再取本轮 paper 子集；同时加载 analysis/existing sections）→ 解析并按角色注入 Skills → pre_agent snapshot → 顺序执行角色/章节子循环 → 创建 step 并绑定 active step id → 每次 `ask`/`ask_streaming` 原子追加 SYSTEM/USER prompt → 保存 result/usage 并流式发布事件 → 角色修改章节时登记 `modified_section_keys` → `_persist()` 从最终正文 marker 重建 citation metadata，并只把 dirty sections 更新 DB/磁盘 → citation/polish/translate → flush disk → post_agent snapshot → 构建自动 quality report → finish run → 成功返回 Job；失败/取消也在已有手稿与审计收口后尽力生成 quality report，再完成 Run/Step/snapshot 持久化 → Renderer 重新加载 run history、document 和 timeline。

每步前检查 token budget 和 cancel；预算读取 durable usage，包含失败调用与 JSON retry。LLM 故障的 outcome/hint/HTTP/retry 字段贯穿 usage 周边审计、step、run、Job 和 SSE；断流文本只进入失败 step output。已完成章节与成功 step 保留，未完成 step 不写成正文；已有非空章节进入 Blackboard 仅表示可作为上下文，并不自动成为本轮输出；`sections_written` 取实际 dirty section 数。`section` 若无 outline 会补 planner/outliner；`stitch` 依赖已有章节；`custom` 必须显式 roles。

自动报告把 citation key/metadata/文献使用/摘要证据/章节目标/双语配对等确定性检查与 CitationAgent/Critic 模型辅助证据分开，`gate` 不能证明语义真实；citation registry 同时提供每篇实际被引论文的 key、题名、DOI/URL/PDF path 与摘要状态。Run 终态后，用户在详情页逐节确认本轮修改、逐篇打开/核对引用来源，并填写事实依据、引用支撑、方法合理性、文献覆盖、论证连贯、写作清晰六项 1–5 分、reviewer 与证据 notes；gate=warn 还需显式 acknowledgement。

post-agent snapshot 后先生成可重放的 `review_manuscript`，再生成含逐节 hash 的 quality report v2。提交评审时后端构建 Rubric v3 target，并重算正文/配对文本长度与 hash、整体 fingerprint、质量报告逐节映射；stale fingerprint 返回 409，缺失/篡改/legacy-unbound 不能 accepted。通过后以 IMMEDIATE transaction 只追加。盲评先从专用 GET 获取去身份 packet；导出原子写项目 `exports/reviews/`。汇总对每 Run latest 计分，同时对不同具名 reviewer 的无序配对计算 kappa/MAD/within-one。无 LLM 时这条审计链仍可用。

恢复有两个显式方向：可重试故障可用原 request 建立新 Run；需要撤销本轮全部已完成修改时，在 Versions 比较/恢复 `result.recovery.restore_snapshot_id`。重跑模型具有不确定性，不等同于 restore；UI 同时提供两者并说明“已完成步骤/章节保留”。质量评审也不改变手稿或 snapshot，只记录当时的人工判断。

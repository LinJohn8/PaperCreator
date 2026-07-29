> 文档用途：一次研究图谱构建与增量修改流程  
> 最后检查：2026-07-28  
> 对应代码：`api/routes/analysis.py`、`analysis/pipeline.py`

# 分析流程

入口按 project、collection 或显式 paper_ids 取 Papers；零输入拒绝。配置由 settings 加 request override。顺序：embedding cache lookup/计算 → fitted reducer 和 scaling → 高维 cluster → cluster keyword/representative/coherence → corpus trends → 3D density/keyword heatmaps → gap detectors → points/result/reducer state 持久化。

异步 job 报阶段进度；同步 API 用于测试/小集合。结果加载可选择 points/heavy fields，前端再按 term 取 heatmap layer。

增量 place idea/paper：确认分析不是语料相对 TF-IDF → 用完全相同的 backend/model 生成 portable embedding → 优先使用保存的 reducer transform/scaling（`method=exact_transform`），失败时才使用同空间缓存向量对现有坐标做近邻插值（`method=interpolated`）→ 计算最近论文/cluster/距离/密度/novelty → 可选写 analysis point/seed。若模型身份改变、无可比缓存或空间不一致则拒绝，不能重算整图后冒充增量，因为那会移动旧点。固定 Hashing+PCA 是无模型、无网络的精确离线路径；TF-IDF 不是。remove-points 只改分析。重跑可能因依赖/模型版本改变坐标，比较前记录 config/warnings。

前端 Library 导入完成后必须 `reloadDocument()`，否则项目 `papers_in_project` 仍是旧值，会错误禁用“构建图谱”。真实 E2E 已覆盖托管 BibTeX 12 篇 → 图谱 12 点 → Idea 13 点 → 移除 12 点 → 重加 13 点 → Electron 重启恢复。

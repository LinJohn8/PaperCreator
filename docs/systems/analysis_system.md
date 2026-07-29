> 文档用途：文献空间分析、可视化数据和缺口候选说明  
> 最后检查：2026-07-28  
> 对应代码：`backend/papercreator/analysis/`、`Landscape3D.tsx`  
> 文档状态：可用；结论需研究者解释

# 分析系统

流程固定为：标题+摘要嵌入（缓存）→ 3D 降维并保存拟合状态 → 在高维 embedding 聚类 → c-TF-IDF 关键词/cluster label → 全局词频/趋势 → KDE 密度和关键词热力层 → 缺口候选 → 保存 points/result。

| 阶段 | 优先实现 | 回退 |
|---|---|---|
| embedding | sentence-transformers 或 LLM embedding（按设置） | TF-IDF/SVD；也可显式选固定 Hashing 以获得完全离线增量定位 |
| reducer | UMAP（可选） | PCA；也支持 t-SNE/MDS |
| cluster | sklearn HDBSCAN（版本可用时） | KMeans/agglomerative；小样本单簇 |
| 关键词 | c-TF-IDF、短语去冗余、年度归一趋势 | 词法统计 |
| 热力 | 3D points 上 KDE grid/切片/keyword layer | 固定网格 |

聚类不在 3D 投影上做，以避免投影伪影；热力和“可见空间缺口”在 3D 上做，因为它们解释用户看到的图。少于 8 篇仍生成图，但跳过完整缺口探测。

当前缺口 detector 覆盖稀疏口袋、cluster bridge、时间停滞等五类，每个候选带 evidence、caveat 和 strength；它们是检索/选题提示，不是“已证明研究空白”。Citation/coauthor graph 会报告覆盖率，引用边缺失时不伪装完整。

Idea/paper 增量定位依赖可复用的 embedding 和 reducer 状态。Sentence-Transformers、LLM embedding 和固定 MD5 bucket 的 Hashing 都是跨语料可比较的 portable embedding；其中 Hashing 每个文本独立归一化、无需模型/网络，质量较弱但可以和 PCA 组成确定性的离线增量路径。语料相对 TF-IDF 会拟合当前 corpus 的词表/IDF/SVD，无法可靠 transform 时明确拒绝而非伪造坐标。

定位优先复用保存的 UMAP/PCA `transform`，返回 `method=exact_transform`；若 reducer 不支持 transform 但仍有同一嵌入空间的缓存，则用近邻加权坐标并返回 `method=interpolated`。UI 分别显示“精确投影”“邻居插值”，不可把二者混成同一可信度。持久化定位只增加 point/seed，原有坐标完全不移动；remove-points 只删分析点，不删除 Paper。

2026-07-28 的真实 Electron E2E 使用 12 篇三主题本地 BibTeX 构建 `hashing:256` + PCA 图谱，把新 Idea 精确投影为第 13 点，移除/重加并在 Electron 重启后恢复 13 点和 seed。该证据验证离线产品路径和持久化，不代表 Hashing 具有语义模型的同义词理解能力。

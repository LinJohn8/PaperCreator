# 术语表

> 最后检查：2026-07-27。

| 术语 | 项目内含义 |
|---|---|
| Workbench | 用户选择的普通文件夹；PaperCreator 只管理其 `.papercreator` 子目录 |
| Managed home | `<workbench>/.papercreator`，完整业务数据/配置/日志/浏览器状态的单根 |
| Project / 新论文 | 一篇正在写的论文工作单元：DB row + `projects/<slug>` + 默认本地 Git（可关闭）；remote 为另行显式选择 |
| Workbench Resource | 复制到 `library/` 的 Idea/论文/代码/数据等输入；不等同 Project |
| Managed copy | 工作台内运行时权威副本；外部原路径只作 provenance |
| Paper | 外部文献、用户 idea 或自有论文的统一记录 |
| Library | 全局 Paper 集合，不等于某 Project collection |
| Collection | Project 内 Paper 关联集合 |
| Landscape / Analysis | 某一 Paper 集合的保存图谱版本 |
| Point | Paper 在某 Analysis 中的坐标/cluster/seed 状态 |
| Gap candidate | 算法证据支持的探索候选，不是已证明研究空白 |
| Blackboard | 单次 Agent run 的项目/文献/分析/章节上下文 |
| Pipeline | Agent 角色执行序列（非数据 ETL 配置） |
| Skill | 可共享的 `SKILL.md` prompt 约束，不是可执行插件 |
| Snapshot | SQLite 保存的手稿恢复点 |
| Flush | DB chapters → project files |
| Reindex | project files → DB chapters |
| Provider | Retrieval 或 LLM 外部来源适配器；需说明上下文 |

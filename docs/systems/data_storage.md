> 文档用途：持久化介质、写读模块、重建与备份  
> 最后检查：2026-07-28  
> 对应代码：`core/db.py`、`core/paths.py`、`store/`  
> 文档状态：可用；DB schema v6 / workbench manifest schema v1 / manuscript sync schema v2

# 数据存储

| 数据 | 路径/表 | 写入 | 读取 | 可重建 | 必须备份 |
|---|---|---|---|---|---|
| 业务索引/状态 | `<workbench>/.papercreator/papercreator.db`，20 个统计表 + FTS5 | store/jobs | 全后端 | 否（部分可重抓） | 是 |
| 项目手稿 | `<home>/projects/<slug>/` | documents/export/skills/git | Editor/Agent/Export/VCS | 否 | 是 |
| 分类输入 | `<home>/library/{ideas,reference-papers,own-papers,code-projects,datasets,supplementary,inbox}` | resources/library API；目录由 `resource_import` Job 原子写入 | Workbench/Library/Agent | 否 | 是 |
| 目录导入 staging | 分类目录直属 `.partial-res_<16hex>` | `store/resources.py` 分块复制 | 仅当前 Job/启动清理 | 是；成功后 rename，失败/取消清理 | 否；发现残留先查 Job/日志 |
| 设置 | `config/settings.json` | settings route | config | 可手配 | 建议 |
| 密钥 | `config/secrets.json` | settings route | config | 否 | 安全方式备份 |
| Skill | `skills/`、项目 `.papercreator/skills` | loader/API | loader/Agent | 用户内容否 | 是 |
| HTTP cache | `cache/http` | HttpClient | retrieval | 是 | 否 |
| embedding cache | DB `embeddings` / `cache/embeddings` | analysis | analysis/incremental | 是 | 否 |
| 模型 | `models/` | 模型下载 | embeddings | 是但大 | 可选 |
| OA PDF | `library/reference-papers/pdfs/` | download job | library/UI | 不保证 | 视需求 |
| Electron 数据 | `electron/` | Chromium | Renderer | 多数可 | 通常否 |
| export | 项目 `exports/` | converters | UI/download | 通常是 | 源稿优先 |
| 手稿同步基线 | 项目 `.papercreator/manuscript-sync.json` | documents flush/reindex | documents/UI/VCS | 可重建，但分叉时不能盲建 | 建议随项目备份 |
| 冲突恢复副本 | 项目 `.papercreator/conflicts/<time>-<id>-<side>/` | documents/VCS | 人工恢复 | 否 | 解决冲突前必须保留 |
| 日志 | `logs/` | logging | UI/维护 | 否 | 通常否 |
| 助手对话 | DB `assistant_threads/assistant_messages/assistant_thread_imports` | assistant chat/治理/导入 API | AssistantPanel/JSON 或 JSON.GZ 归档 | 否 | 是；含可能敏感的项目上下文对话和归档来源审计 |
| 翻译预览 | DB `jobs.result`（`kind=translation`） | translation Worker/apply | Editor/Output | 源文可重做但公共请求可能不可复现 | 完成未应用预览按需备份；应用后由手稿+snapshot 保护 |

SQLite schema v6 的主要表另含 `assistant_threads/assistant_messages/assistant_thread_imports`；`papers_fts` 为 FTS5 虚表。v3 增加双语目标，v4 增加提示词模板，v5 增加独立工作台/项目对话和线程内有序消息，v6 增加作用域+来源线程 ID+完整内容 fingerprint 的幂等导入映射。迁移数组只追加，不得修改已发布 v1-v6 SQL。

多格式自有论文的短预览仍在 Resource/Paper metadata，最多 80,000 字符；完整提取文本 sidecar 位于 `.papercreator/cache/extracted/<resource_id>.txt`，可从原托管文件重建。删除 Resource 托管文件时只允许删除对应可重建 sidecar，不得用任意 metadata 路径删除文件。

项目手稿导入源位于 `<project>/.papercreator/imports/`，用于复核和恢复，不能当 HTTP cache 清理。投稿 ZIP 解压到 `<project>/assets/venue-templates/<slug>/`；审计清单 `<project>/.papercreator/venue-templates.json` 应随项目备份。提示词模板存 SQLite：工作台模板 `project_id IS NULL`，项目模板使用外键级联删除。助手线程同样以 nullable `project_id` 区分工作台/项目；项目删除级联线程、消息与导入映射。归档恢复生成新本地 ID，不覆盖现有线程；原线程被删后同一来源可恢复，来源内容改变则作为新副本。消息清理把 content/actions/meta 一并替换，只留下 SHA-256、大小、时间和原因。retention 默认 `0`，只驱动显式预览，不会在启动时自动删数据。

`translation` Job 的 payload 只保存项目/章节 id、source SHA-256、字符数和 Provider 边界，不复制源明文；完成 result 保存完整译文预览与 source/paired fingerprints。运行中进程退出会按通用 orphan Job 规则标 failed，不自动重发公共文本；完成预览跨重启可读。apply 后 result 记录 snapshot/applied_at，重复 apply 幂等。

Agent 失败与质量证据都不是临时日志：`agent_runs.result` 保存 request、diagnosis、pre/post snapshots、quality report v2、可重放 `review_manuscript`、双 fingerprints 和 append-only `human_evaluations[]`。冻结全文会使每个 Run 约增加一份手稿大小，这是精确历史审阅的有意成本。blind/analysis JSON 是项目 `exports/reviews/` 中的可重建导出；SQLite 内原始 Run 证据与人工记录不可重建、不可当 cache 清理。旧 v1/v2 原样兼容；Rubric v3 追加仍使用 `BEGIN IMMEDIATE`。summary 可从记录重算。

分类目录导入不会直接写最终路径。预扫描和空间检查后，在目标分类同盘建立严格命名的 staging，4 MiB 分块复制并同步摘要；全部源身份/目录校验通过才原子 rename，之后才登记 ready 资源。`workbench_resources.metadata.import` 保存 strategy、source/copied files/bytes、excluded count、link policy 和 space preflight，属于导入审计而非临时 UI 数据。取消/失败不登记 ready row；异常终止留下的严格 `.partial-res_<16hex>` 由下次启动回收，相似名称的用户目录不会自动删除。

推荐备份是停止应用后复制整个 `<workbench>/.papercreator/`，不要只复制主 `.db` 而忽略活动 WAL/SHM。只备份 projects 会丢文献/资源/分析/运行状态；只备份 DB 会丢手稿、同步基线、冲突副本、托管资料和 Git。工作台资源的 `managed_path` 相对 home，可随完整目录移动；`original_path` 只用于来源追溯。

> 文档用途：手稿、模板、双语和引用系统  
> 最后检查：2026-07-28  
> 对应代码：`writing/`、`store/documents.py`、`api/routes/writing.py`  
> 文档状态：可用

# 写作系统

当前有 11 个原创内容结构模板：generic、survey、empirical、short、thesis-chapter、sci-imrad、ssci-empirical、conference-full、systematic-review、research-poster、book-chapter。它们定义章节 key/title/中文标题、双语目标和 guidance，不包含出版方 `.cls/.sty`，也不宣称是统一“SCI/SSCI 排版模板”。创建项目可应用结构；替换已有非空内容需要明确许可。

`DocumentModel` 由有序 `SectionModel` 组成。每节有 primary `content`、`content_zh`、guidance、`target_words`、`target_words_zh` 和 cited paper ids；DB v3 为双语独立目标增加迁移。UI 支持新建、删除、改双语标题、改双目标、排序、状态、要求和空手稿首节；目录分别显示主语言与对照语言 `当前/目标`。语言交换、保存对照草稿和覆盖已有译文都是显式操作。统计同时计英文 token-like words 和 CJK 字符。

## 多格式导入

`importers/document_text.py` 支持 PDF（pypdf）、DOCX（标准库 OOXML）、Markdown、TXT 和 TeX，最大提取 2,000,000 字符。自有论文进入工作台资源时，文献库预览最多 80,000 字符，完整可重建 sidecar 写入 `.papercreator/cache/extracted/<resource_id>.txt`；metadata 保存方法、页数、字符数、截断、告警及检测标题/作者。

扫描 PDF 默认只报告无文字层；用户显式启用后，本地 OCR adapter 使用 Tesseract 和 `pypdfium2` 或 `pdftoppm`，先探测可执行文件/语言包，只补文字少于阈值的页面，限制 1–200 页、默认 50 页、200 DPI 和逐页超时，不修改原 PDF，也不联网。当前开发机缺少真实引擎/渲染器，因此只有 mock 合同，不能把 OCR 标为本机 live 已验收。

DOCX 只读取有界 OOXML part（正文 32 MiB、辅助 part 8 MiB），按块保留表格行、普通段落、换行/制表、脚注/尾注引用，并把常见 OMML 分数、上下标、根式、函数、定界符、n-ary 和矩阵转成稳定线性数学文本；metadata 记录 tables/equations/footnotes/endnotes 数量。exact golden 验证表格、公式和脚注不再静默丢失，但复杂 Word 版式仍需人工复核。无 Pandoc 的内置 DOCX 导出会把公式保留为可编辑字面文本，不冒充原生 Word 公式排版。

项目手稿导入由 `writing/manuscript_import.py` 实现两阶段协议。preview 校验≤100 MB、提取内容、按 Markdown 标题或常见学术标题建议拆节，并返回源 SHA-256 与短摘录；apply 不让全文经过 Renderer 往返，先复核 SHA-256，再按用户选择追加或替换。替换需要二次确认并先建 snapshot；导入源复制到 `<project>/.papercreator/imports/`，章节 key 冲突会生成新 key。该目录是审计输入，不是全局文献缓存。

## 翻译

`GET /api/writing/translation/providers` 暴露三类能力：离线专业术语表、MyMemory 公共服务、已配置 LLM。选词术语查询可离线；单节/批量全文翻译可用 MyMemory 或 LLM。MyMemory 只有在用户选择并勾选公共外发确认后才联网；同步入口限 10,000 字符，超过后使用 `translation` Job，单 Job 最多 100,000 字符和 250 请求。分块优先句段边界，代码围栏/展示公式与分隔空白不外发；请求限速，429/5xx/timeout/network 有有界重试并遵守 Retry-After，块间可取消。

批量 Job 不逐节写手稿，只在 `jobs.result` 保存全部译文、源/目标 SHA-256、请求/重试统计和 `preview_only=true`。失败或取消时所有章节保持原样；完成预览可在重开弹窗后恢复。用户检查并确认 apply 后，后端先拒绝任意陈旧源文/对照文或磁盘冲突，创建手动恢复快照，再在同一 DB 事务中更新所有 `content_zh` 并 flush；相同 Job 重复 apply 返回幂等结果。

## 投稿排版包

`writing/venue_templates.py` 允许用户导入自己从 publisher、会议或 Overleaf Gallery 获得且有权使用的 ZIP。preview/apply 检查绝对路径、`..`、Windows 盘符、符号链接、加密条目、最多 5000 条目、ZIP≤100 MB、展开≤200 MB，并在 apply 时复核 SHA-256。文件写入 `<project>/assets/venue-templates/<slug>/`；`.papercreator/venue-templates.json` 记录名称、来源 URL、许可证、摘要、时间和文件数。导入不编译或执行包内文件；没有授权确认就拒绝。

SQLite 是 UI 编辑状态，项目 `manuscript/NN-key.md|tex`、assembled manuscript、metadata 和 bibliography 是可读/Git 镜像。`flush` 写磁盘；`reindex` 从编号章节文件覆盖 DB，后者用于 checkout/外部修改。`full.md`/`full.tex` 只是每次 flush 重建的合并预览，不是 reindex 输入，不能作为外部编辑入口。

`store/documents.py` 在项目 `.papercreator/manuscript-sync.json` schema v2 保存每个文档最近一次确认同步时的整体摘要，以及每个章节的 filename/DB/disk fingerprint。`sync_status()` 可区分 `in_sync`、单侧变更、双侧分叉、旧项目无基线但相等/单侧/分叉等状态，并返回两侧修改章节集合与 merge blockers。普通 flush 在磁盘侧变过时拒绝，普通 reindex 在 DB 侧变过时拒绝；API 返回 409 `manuscript_sync_conflict`。

仅当 DB/磁盘都只改了互不重叠的既有 section key、无增删/重命名且 preview token 仍绑定当前状态时，`POST /api/writing/{project_id}/merge-disjoint` 才允许合并：磁盘修改进入 DB，DB 修改写到对应文件，操作前建立 DB snapshot 并备份 DB/磁盘镜像。同节双改、旧 schema v1 基线和结构变化不自动猜测。用户仍可显式选“以数据库为准”或“以文件为准”；强制操作先把确有未同步修改且即将被覆盖的一侧复制到项目 `.papercreator/conflicts/`，文件覆盖 DB 另建 snapshot。

Editor 每 5 秒读取同步状态，在冲突横幅中提供两个方向和“查看文件”。为避免用户刚按保存的 UI 文本丢失，章节 PATCH 会保留 DB 更新，但 flush 不覆盖已变磁盘，并以 409 告知进入 `diverged`；客户端不能把这个 409 当作事务回滚。数据库优先成功后只清除与重新加载 DB 内容完全相同的 dirty 值，保留请求期间更晚输入；文件优先先清 dirty、reindex 后加载磁盘正文。`CodeEditor` 给外部 value reconciliation 标注专用 CodeMirror transaction annotation，不把程序性替换送入用户 `onChange` 或 undo history，避免已持久化文本再次显示未保存。Agent 落库、Overleaf apply、snapshot restore、Git checkout/discard 这类批量覆盖入口则在第一次业务修改前保护；Git discard 额外保存 binary patch 并在完成后 reindex。旧项目没有基线且两侧不同时不会猜测正确来源。上述 409→diverged→DB 优先 backup→disk 优先 snapshot→恢复正常保存链已有真实 Electron E2E。

引用系统以 `CitationKeyMap` 作为 prompt、Agent 质量检查和导出的唯一 author-year key 实现。碰撞从无后缀基础键开始，再按完整项目 registry 稳定分配 `a`、`b`……；分章节只选择某个 paper 子集时也不重新编号。不存在的 `[KEY]` marker 会报告而不是静默删除，bibliography 按首次出现排序。Agent 每次最终落库都从 Reviser/Polisher 后的 primary text 重建 `cited_paper_ids`，正文是权威来源；双语翻译不改变 citation key。

限制：不同章节修改可以安全集合合并，但同节内容没有自动三方 diff/merge；无 CRDT；复杂 LaTeX/Word 排版回导有损。Rubric v3 能精确证明评审绑定哪版正文/摘要并要求逐篇核对，但 hash、自动 gate 和人工 accepted 都不能证明具体论断绝对真实。

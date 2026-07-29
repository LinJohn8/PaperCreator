> 文档用途：当前 FastAPI 实际路由清单、输入输出与副作用  
> 最后检查：2026-07-28  
> 对应代码：`backend/papercreator/api/routes/`、`apps/desktop/src/api/endpoints.ts`  
> 文档状态：由当前 app routes/OpenAPI 交叉核对

# API 参考

Base URL：默认 `http://127.0.0.1:8765`，CLI `--host/--port` 会成为实际 app/lifespan/CORS/热重载 endpoint。交互式 OpenAPI：`GET /api/docs`，schema：`GET /api/openapi.json`。2026-07-28 从当前源码读取：14 个业务 router、157 个唯一路径、181 个 HTTP 操作，并有 method/path SHA-256 snapshot。另有一个不进入 OpenAPI、只接受 Electron 每次启动随机 capability 的内部关机路由。下表所有接口均存在于当前代码；没有用户鉴权，只可在本机回环使用。本轮暂未重新打包，因此不把旧冻结后端计数冒充当前源码。

成功一般直接返回 JSON object；业务错误统一为：

```json
{"error":{"code":"validation_error","message":"actionable text","details":{}}}
```

Pydantic schema 的逐字段默认值以 OpenAPI 为最终机器可读合同。表中 `写` 表示会修改 DB、文件、缓存、进程或外部服务；`任务` 表示立即返回 `job_id`。

## System

| 方法/路径 | 输入 | 返回/副作用 | 前端/状态 |
|---|---|---|---|
| GET `/api/system/health` | 无 | 版本、paths、DB、jobs、Provider/LLM/analysis/export/git 状态 | boot/轮询；可用 |
| GET `/api/system/paths` | 无 | resolved data paths | 封装外可调 |
| GET `/api/system/capabilities` | 无 | UI 选项总表 | 封装；可用 |
| GET `/api/system/events` | `after` | SSE replay/live stream | 全局使用 |
| GET `/api/system/jobs` | project_id/status/limit | job rows | Status/Output |
| GET `/api/system/jobs/{job_id}` | path | 单 job 或 404 | 封装 |
| POST `/api/system/jobs/{job_id}/cancel` | path | 写：协作取消请求 | Output |
| GET `/api/system/logs` | which=main/errors, lines≤5000 | 日志尾部 | Output |
| POST `/api/system/maintenance` | vacuum/prune/clear flags | 写：缓存/台账/DB 维护 | Settings |
| GET `/api/system/cache` | 无 | HTTP/embedding cache stats | Settings |
| GET `/api/system/usage` | days≤365 | LLM token/cost 汇总 | Settings |
| POST `/api/system/shutdown` | 内部 `X-PaperCreator-Shutdown` capability | 写：设置 owned Uvicorn `should_exit`，进入 lifespan/checkpoint；错误/缺失/非桌面 callback 均 404 | Electron main only；不进 OpenAPI/Renderer，不是公共自动化 API |

## Settings

| 方法/路径 | 输入 | 返回/副作用 |
|---|---|---|
| GET `/api/settings` | 无 | 脱敏完整 Settings |
| GET `/api/settings/sources` | 无 | 配置优先级、文件/环境来源和被覆盖字段名；不返回值 |
| PATCH `/api/settings` | 任一 settings section；含 `assistant.retention_days=0..3650` | 写 JSON/secrets + reload；空 patch 422；retention 默认 0 且本身不执行删除 |
| POST `/api/settings/reload` | 无 | 刷新 `.env`，再从 disk/env 重载 |
| DELETE `/api/settings/secret` | query `path` | 写：删除指定 secret |
| GET `/api/settings/llm/providers` | `probe` | Provider 列表/可选联网探测 |
| POST `/api/settings/llm/test` | provider_id/model | 外部调用；成功返回 reply/usage/duration，失败返回 outcome/error_code/retryable/HTTP/retry-after/hint/provider/model |
| PUT `/api/settings/llm/providers/{provider_id}` | kind/base/key/model/price… | 写 Provider config |
| GET `/api/settings/retrieval/providers` | 无 | Provider 能力和 enabled |
| PUT `/api/settings/retrieval/enabled` | `provider_ids` | 写 enabled，禁止全空 |
| GET `/api/settings/analysis/backends` | 无 | backend/reducer/clusterer 可用性和 blocker |
| POST `/api/settings/analysis/probe-model-host` | 无 | 外部探测模型 host/cache |
| GET `/api/settings/overleaf` | 无 | 脱敏配置和本机能力 |

## Workbench

| 方法/路径 | 输入 | 返回/副作用 | 前端/状态 |
|---|---|---|---|
| GET `/api/workbench` | 无 | 工作台根、`.papercreator`、projects、7 类目录/计数、磁盘空间、规则 | Workbench 首页/启动恢复；可用 |
| PATCH `/api/workbench/state` | `last_project_id` | 写 `app_state`；非空 id 必须存在 | Store 打开/关闭项目；可用 |
| GET `/api/workbench/resources` | kind/project_id/limit≤2000 | 资源、绝对展示路径、exists | Workbench 首页；可用 |
| POST `/api/workbench/resources` | kind、source_path 或 content、metadata、可选 project_id | 写：托管复制/Markdown、SHA-256、DB；论文类可解析/建立 Paper 与 Collection | 分类导入；可用 |
| POST `/api/workbench/resources/import` | code_project/dataset/supplementary/inbox + directory `source_path` | 202 + `resource_import` job；预扫描/空间检查/分块复制/摘要/同盘 staging/原子 rename/最后登记 | Workbench 目录导入；进度由 Job/SSE，支持取消 |
| DELETE `/api/workbench/resources/{resource_id}` | `remove_files=false` | 写：默认只忘记注册；显式 true 仅可删 `library/` 内托管文件 | endpoint 已封装；当前首页无删除按钮 |

资源类型只有 `idea`、`reference_paper`、`own_paper`、`code_project`、`dataset`、`supplementary`、`inbox`。写作 Project 不属于资源，单独位于 `projects/`。目录 API 不接受论文/Idea 或普通文件；它在任何 ready row 出现前复制到保留名 `.partial-res_<id>`，失败/取消清理，重启回收严格匹配的残留。嵌套 symlink、junction/reparse point 和特殊文件不跟随并写入 audit metadata；代码目录另排除依赖/虚拟环境/构建目录及 `.env*` 密钥文件。`jobs.result.resource` 是完成后的标准 Resource，`metadata.import` 包含 strategy、source/copied files/bytes、excluded count、link policy 和 space preflight。

## Projects 与 Collections

| 方法/路径 | 输入 | 返回/副作用 |
|---|---|---|
| GET `/api/projects` | status | projects + 可导入目录 |
| POST `/api/projects` | title、idea、template、language、git… | 写：row/`projects/<slug>`/collection/template/Git |
| GET `/api/projects/{project_id}` | path | project + document/stats/collections/git |
| PATCH `/api/projects/{project_id}` | 可变 project fields | 写 row/metadata |
| DELETE `/api/projects/{project_id}` | `remove_files` confirm | 写；可删除 `projects/` 内项目目录，危险 |
| POST `/api/projects/{project_id}/relocate` | `{path}` | 写：更新/移动项目定位，路径校验 |
| POST `/api/projects/import` | `{path,reindex}` | 写：导入已有目录 |
| GET `/api/projects/{project_id}/collections` | path | collection list |
| POST `/api/projects/{project_id}/collections` | name/kind/description | 写 collection |
| POST `/api/projects/{project_id}/collections/{collection_id}/papers` | `{paper_ids}` | 写 links |
| DELETE `/api/projects/{project_id}/collections/{collection_id}/papers` | `{paper_ids}` | 写 links |
| DELETE `/api/projects/{project_id}/collections/{collection_id}` | path | 写 collection/link；不删 Paper |
| GET `/api/projects/{project_id}/papers` | collection/text/sort/page | project papers |

## Search

| 方法/路径 | 输入 | 返回/副作用 |
|---|---|---|
| GET `/api/search/providers` | 无 | 9 Provider 能力/当前可用性；Search 页挂载时调用，导入本地文献后会变化 |
| POST `/api/search` | SearchBody | 写/任务：返回 job_id；请求原样进入后台 Job，完成后写文献/项目关联/历史 |
| POST `/api/search/sync` | SearchBody | 写：同步 SearchResponse，适合小请求/测试；与异步入口使用同一 pipeline 合同 |
| POST `/api/search/expand` | query/seed/use_llm/max_queries | 可外部 LLM；返回 queries/terms/method |
| POST `/api/search/resolve` | identifier/providers/add_to_project | 外部查找并写 library |
| GET `/api/search/history` | project_id/limit | search rows；桌面默认读取最近 20 条 |
| GET `/api/search/history/{search_id}` | path | 保存的 request/results/stats |
| DELETE `/api/search/history/{search_id}` | path | 写：删历史，不删 Papers |
| POST `/api/search/history/{search_id}/rerun` | path + `use_cache=false` | 写/任务：按历史 request 重跑并建立新历史；只覆盖 cache 选择，保留 mode/seed/providers/`use_llm_expansion` |

SearchBody 关键字段：`query`、`mode=keyword|idea|paper`、`seed_text`、`providers`、limits、year range、OA/venue/author/field/exclude/sort、project/collection、`use_cache`、`use_llm_expansion`。后两个布尔值均是可复现请求的一部分；`use_llm_expansion=false` 必须让正式后台检索和后续 rerun 都使用规则展开，而不只是让 `/expand` 预览显示规则结果。对应前端为 `apps/desktop/src/views/SearchView.tsx`，状态动作在 `state/store.ts::runSearch`。

`SearchResponse` 返回 `search_id/query/mode/papers/stats/dedupe counts/warnings/request`。`request` 是 pipeline 展开后的实际执行快照；即使全部 Provider 失败，`persist=true` 仍返回非空 `search_id` 并可由 history 取回。每个 `ProviderStats` 包含 `provider/count/duration_ms/outcome/error/error_code/retryable/http_status/retry_after_s/hint/cache/query/truncation`；稳定 outcome 枚举见 [systems/retrieval_system.md](systems/retrieval_system.md)。history row 的 `provider_stats` 是以 Provider id 为 key、去掉重复 `provider` 字段的映射。

SSE `search.provider` 使用 camelCase：`provider/providerName/count/durationMs/outcome/error/errorCode/retryable/httpStatus/retryAfterS/hint/completed/total`。所有成功、零命中、全失败和无可用源路径最终都发布 `search.done`；其 payload 至少包含 `searchId/count/beforeDedupe/merged/durationMs`，失败路径另含 `failedProviders` 或 `unavailableProviders`。

## Library

| 方法/路径 | 输入 | 返回/副作用 |
|---|---|---|
| GET `/api/library` | text/project/collection/year/origin/status/tag/rating/OA/sort/page | papers + total |
| GET `/api/library/stats` | 无 | counts/tags |
| GET `/api/library/{paper_id}` | path | Paper |
| PATCH `/api/library/{paper_id}` | editable metadata/user fields | 写 Paper/FTS |
| DELETE `/api/library/{paper_id}` | path | 写：删 Paper 和关联 |
| POST `/api/library/delete` | `{paper_ids}` | 写：批量删除 |
| POST `/api/library/tag` | paper_ids/add/remove | 写 tags |
| POST `/api/library/papers` | title/abstract/authors/origin/project… | 写 manual/idea/own paper |
| POST `/api/library/import` | path/project/collection | 写：先复制到 `library/reference-papers`，再解析 Bib/RIS/CSV/JSON |
| GET `/api/library/duplicates` | project_id/threshold | 候选 duplicate groups |
| POST `/api/library/merge` | keeper_id/duplicate_ids/confirm | 写：合并并删重复行 |
| POST `/api/library/download-pdfs` | paper_ids | 写/任务：OA PDF 到 pdf dir |

## Analysis

| 方法/路径 | 输入 | 返回/副作用 |
|---|---|---|
| GET `/api/analysis/capabilities` | 无 | 可选包/backend/detectors |
| POST `/api/analysis` | project/paper ids + config overrides | 写/任务：analysis job |
| POST `/api/analysis/sync` | 同上 | 写：同步 AnalysisResult |
| GET `/api/analysis` | project_id/limit | analysis summaries |
| GET `/api/analysis/{analysis_id}` | include_points/heatmap/layers/papers | AnalysisDetail |
| GET `/api/analysis/{analysis_id}/layer/{term}` | path | 单 keyword heatmap grid |
| GET `/api/analysis/{analysis_id}/papers` | path | point-paper rows |
| DELETE `/api/analysis/{analysis_id}` | path | 写：删 analysis/points，不删 Papers |
| POST `/api/analysis/{analysis_id}/place-idea` | title/abstract/keywords/project/persist | 写可选：PositionResult |
| POST `/api/analysis/{analysis_id}/place-paper` | paper_id/persist/mark_as_seed | 写可选：PositionResult |
| POST `/api/analysis/{analysis_id}/remove-points` | paper_ids | 写：只删 points |
| GET `/api/analysis/{analysis_id}/graph` | path | analysis citation/coauthor graph |
| GET `/api/analysis/project/{project_id}/graph` | path | project graph |
| POST `/api/analysis/{analysis_id}/label-clusters` | cluster_ids/model | 外部 LLM + 写 labels |

`PositionResult` 已作为两个定位路由的显式 OpenAPI response model，包含 `point`、最近 cluster/论文、密度、novelty、邻近 gap 与双语解释；`method` 是必填枚举：`exact_transform` 表示复用了保存的 UMAP/PCA transform，`interpolated` 表示无法 transform 时按同嵌入空间近邻加权。客户端必须展示这个差别。`GET /api/analysis/capabilities` 的 `embedding_backends[].portable` 当前对 Hashing 为 `true`、TF-IDF 为 `false`；前者支持离线增量定位，后者必须把新论文纳入后重建图谱。

## Writing

| 方法/路径 | 输入 | 返回/副作用 |
|---|---|---|
| GET `/api/writing/templates` | 无 | 11 个原创内容结构模板；不是出版方官方排版包 |
| GET `/api/writing/import/ocr-capabilities` | 无 | 本机 Tesseract/渲染器/语言包与页限；只报告能力，不安装或联网 |
| POST `/api/writing/import/preview` | `source_path`、可选 `project_id/use_ocr/ocr_languages/ocr_max_pages` | 只读：校验≤100 MB，提取 PDF/DOCX/MD/TXT/TeX；OCR 仅显式启用并只补无文字层页；返回 SHA-256、章节建议和最多 2000 字摘录 |
| POST `/api/writing/{project_id}/import` | 预览返回的 path/hash、选中章节、`mode=append|replace`、确认标志 | 写：复核源 SHA-256；复制到项目 `.papercreator/imports/`；replace 需确认并先建恢复快照 |
| POST `/api/writing/venue-template/preview` | ZIP `source_path` | 只读：检查路径穿越、盘符、符号链接、加密条目、5000 条/100 MB/展开 200 MB 上限 |
| POST `/api/writing/{project_id}/venue-template` | path/hash/name/source_url/license/`confirm_license` | 写：复核 SHA-256，安全解压至 `assets/venue-templates/<slug>/`，更新 `.papercreator/venue-templates.json` |
| GET `/api/writing/translation/providers` | 无 | 离线术语表、MyMemory、当前 LLM 可用性和隐私/费用说明 |
| POST `/api/writing/translate` | text/source/target/provider/`confirm_external` | 离线术语、≤10k MyMemory 或已配置 LLM 同步翻译；MyMemory 必须确认；不写手稿 |
| POST `/api/writing/translation/jobs` | text 或 project/section keys、provider、overwrite、`confirm_external` | 202：长文/批量翻译 Job；只保存完整预览，不写手稿；MyMemory ≤100k 字符/250 请求 |
| POST `/api/writing/translation/jobs/{job_id}/apply` | `confirm=true` | 仅项目预览；校验 source/paired SHA-256 与磁盘同步，建手动快照，同事务更新全部 `content_zh` 并 flush；重复调用幂等 |
| POST `/api/writing/{project_id}/template` | template_id/target_words/replace | 写 sections；replace 受保护 |
| GET `/api/writing/{project_id}/document` | include_content | document + stats |
| GET `/api/writing/{project_id}/sections/{section_key}` | path | Section |
| PATCH `/api/writing/{project_id}/sections/{section_key}` | content/content_zh/title/title_zh/status/guidance/`target_words`/`target_words_zh`… | 写 DB/disk sync；中英文目标独立 |
| POST `/api/writing/{project_id}/sections` | key/title/title_zh/order/content/双目标… | 写 section；空手稿也可建立首节 |
| DELETE `/api/writing/{project_id}/sections/{section_key}` | path | 写 section |
| POST `/api/writing/{project_id}/reorder` | ordered_keys | 写 ordering |
| GET `/api/writing/{project_id}/stats` | path | word/citation/section stats |
| GET `/api/writing/{project_id}/bilingual` | path | translation completeness |
| POST `/api/writing/{project_id}/swap-languages` | 无 | 写：交换 primary/paired |
| GET `/api/writing/{project_id}/assembled` | language | assembled text/blocks |
| POST `/api/writing/{project_id}/bibliography` | cited_only | 写 bibliography |
| GET `/api/writing/{project_id}/sync-status` | 无 | DB/磁盘摘要、基线、变更侧和允许方向；只读 |
| POST `/api/writing/{project_id}/merge-disjoint` | preview token、`confirm=true` | 仅 sync v2 且 DB/磁盘修改既有章节集合完全不重叠时写：先建 DB snapshot/两侧镜像，再合并并刷新基线；陈旧/同节双改/结构变化 409 |
| POST `/api/writing/{project_id}/flush` | `force=false` | 写：DB→磁盘；磁盘已变时 409，force 先备份磁盘 |
| POST `/api/writing/{project_id}/reindex` | `force=false` | 写：磁盘→DB；DB 已变时 409；force 一定先 snapshot，DB 未同步时另备份镜像 |

同步冲突统一返回 HTTP 409，`error.code=manuscript_sync_conflict`，`details.sync` 含逐节修改集合/merge blocker，`details.resolution` 给出显式方向。章节 PATCH 会先更新 DB，再可能在 flush 阶段因磁盘变化返回 409，因此该 409 **不表示 DB 回滚**。checkout/restore/Agent/Overleaf 等批量覆盖入口在首次修改前 preflight。基线是 schema v2 `.papercreator/manuscript-sync.json`；v1 兼容但无逐节 merge 资格。所有合并/强制解决的恢复材料保存在 snapshot/`.papercreator/conflicts/`。

MyMemory 是无需本项目 API key 的第三方公共服务：发送的文本会离开本机，受其额度、隐私条款和可用性约束。LLM 翻译按用户已配置 Provider 计费。离线术语表只处理已收录的专业词汇，不应被描述为全文机器翻译。

## Project assistant

| 方法/路径 | 输入 | 返回/副作用 |
|---|---|---|
| GET `/api/assistant/threads` | `project_id`，空值为工作台作用域 | 最近 100 个线程（每线程消息/字符/估算字节/活动统计）+ 范围汇总 |
| POST `/api/assistant/threads` | project_id/title | 新建空线程 |
| GET `/api/assistant/threads/export` | `project_id` | 版本化范围 JSON：统计、全部线程、完整消息/actions/meta；不含设置密钥 |
| POST `/api/assistant/threads/import/preview` | `project_id` + 版本化 archive object | 只读：验证 archive/作用域，按来源 ID+完整内容 fingerprint 返回新增/跳过统计与 preview token |
| POST `/api/assistant/threads/import/execute` | preview 字段、archive、`confirm=true` | 同一写事务重算；新线程使用新本地 ID，相同来源幂等跳过，删除后可恢复，来源内容变化建新副本 |
| POST `/api/assistant/threads/maintenance/preview` | project_id、`all|retention`、older_than_days | 返回候选统计、精确 cutoff 和覆盖全部候选内容的 SHA-256 preview token；不删除 |
| POST `/api/assistant/threads/maintenance/execute` | preview 的 scope/mode/cutoff/token、`confirm=true` | 同一写事务重算 token；变化则拒绝重新预览，否则仅删除该范围候选 |
| GET `/api/assistant/threads/{thread_id}` | limit≤1000 | 线程与严格 ordering 消息 |
| DELETE `/api/assistant/threads/{thread_id}` | path | 显式删除线程并级联消息 |
| POST `/api/assistant/messages/{message_id}/redaction/preview` | 无 | 返回绑定当前 content/actions/meta 的 preview token、原大小/哈希；不修改 |
| POST `/api/assistant/messages/{message_id}/redaction/execute` | token、reason、`confirm=true` | 不可逆替换 content/actions/meta；变化则拒绝；只留 SHA-256、大小、时间、原因审计，导出不含原文 |
| POST `/api/assistant/chat` | message≤40k、project/section/thread、skill ids、locale、model | 外部 LLM；成功后原子追加一轮；返回回答、usage、上下文和建议动作；不执行动作 |

助手读取有限项目上下文和已选 Skill。线程与请求项目作用域不符时拒绝；成功轮次两条消息同事务保存，模型失败不留下半轮。桌面文件归档另由窄 IPC 限制为 JSON/JSON.GZ，压缩前后 256 MiB，Renderer 无任意文件读取。保留天数默认 `0`，从不静默删除。恢复 actions 仍须 Renderer 二次确认；持久化不等于已执行，本地提交永不等于 push。

## Prompt templates

| 方法/路径 | 输入 | 返回/副作用 |
|---|---|---|
| GET `/api/prompts` | 可选 `project_id` | 工作台模板 + 当前项目模板 |
| POST `/api/prompts` | name/content/description/project_id | 写 SQLite；识别 `{{variable}}` |
| PUT `/api/prompts/{template_id}` | 完整模板字段 | 写：更新模板和变量清单 |
| DELETE `/api/prompts/{template_id}` | path | 写：删除模板 |

模板内容上限 200,000 字符。工作台模板 `project_id=NULL`；项目模板随项目级联删除。复制、粘贴、变量填充和插入助手输入框是前端动作，数据权威在 SQLite，不在 `localStorage`。

## Agents

| 方法/路径 | 输入 | 返回/副作用 |
|---|---|---|
| GET `/api/agents/pipelines` | 无 | 4 pipelines + 11 roles |
| POST `/api/agents/run` | project/pipeline/model/options/sections/skills/budget | 写/任务：run_id + job_id |
| GET `/api/agents/runs` | project/status/limit | run rows |
| GET `/api/agents/evaluations/summary` | `project_id`、`limit=500`（1–2000） | latest 聚合、append-only 分歧，以及不同具名 reviewer 的 exact/kappa/MAD/within-one/quadratic weighted kappa |
| GET `/api/agents/runs/{run_id}` | include_prompts | run + steps |
| GET `/api/agents/runs/{run_id}/review-packet` | `kind=blind|analysis` | blind 包隐藏 run/project/model/pipeline/reviewer/历史决定和本地 PDF path；analysis 包恢复 provenance、成本与全部评审 |
| POST `/api/agents/runs/{run_id}/review-packet/export` | `kind=blind|analysis` | 原子写入项目 `exports/reviews/<sample>-<kind>.json`，返回 path/packet fingerprint/bytes |
| GET `/api/agents/runs/{run_id}/steps/{step_id}` | path | step detail |
| POST `/api/agents/runs/{run_id}/evaluations` | 前述字段 + `reviewed_manuscript_fingerprint`、`review_mode=identified|blind` | 写：终态 Run 可追加 revision/rejected；v3 accepted 必须绑定完整、未篡改的冻结正文；陈旧表单返回 409；只追加不覆盖 |
| POST `/api/agents/runs/{run_id}/cancel` | path | 写：协作取消 |
| DELETE `/api/agents/runs/{run_id}` | path | 写：删 run/steps，不恢复手稿 |
| POST `/api/agents/preview` | project/role/section/skills/analysis/context | 只构建 prompt，无 LLM 调用 |
| GET `/api/agents/blackboard/{project_id}` | analysis_id/paper ids | 黑板摘要 |

Run/step 审计语义：`GET .../steps/{step_id}` 返回该 step 实际累计发送的 prompt 与模型 output；若 Reader 等一步内有多次 LLM 调用，prompt 以 `===== NEXT LLM CALL =====` 分隔并原子追加，不以最后一次覆盖前面的调用。Agent run 只把本轮登记到 `modified_section_keys` 的章节计入 `result.sections_written` 并写回手稿；Blackboard 中已有章节只作上下文。prompt 可能包含论文摘要和用户文本，设置页的维护操作可清理旧 run/prompt，导出或共享前应按敏感数据处理。

每个 Agent 终态的 `result.quality_report` 使用 schema v2。每节增加 primary/paired text 的 SHA-256 与字符数；citation registry 为实际被引 Paper 固化 key/title/year/DOI/URL/PDF path、冻结摘要、摘要 SHA-256 与 availability。`result.review_manuscript` 另内嵌当时全稿的 primary/paired 原文、逐节哈希、modified 标志、post-agent snapshot id 与整体 manuscript fingerprint。只存 hash 不足以重放历史正文，因此原文与 hash 必须同时保存。

人工 Rubric 六项仍为 1–5。Rubric v3 的 `accepted` 在 v2 条件之外，必须满足 quality report schema≥2、`review_manuscript` 逐字段重算通过、提交的 manuscript fingerprint 与服务器当前冻结证据完全相同。快照缺失/篡改分别产生 `immutable_manuscript_missing`、`immutable_manuscript_integrity_failed`、`reviewed_manuscript_fingerprint_mismatch` blocker；已打开表单指向不同 fingerprint 时返回 409 `agent_evaluation_stale_manuscript`。failed/cancelled、`fail|unavailable` 或 legacy-unbound Run 仍只能 revision/rejected。

每条 v3 `review_target` 同时固化 `quality_report_fingerprint`、`manuscript_fingerprint`、manuscript schema/source snapshot/section count 与完整性结果。旧 rubric v1/v2 历史原样保留；无正文绑定的旧 Run 继续按 rubric v2 记录 revision/rejected，但不能新写 v3 accepted。后续项目正文修改或追加评审都不会改变 Run 内冻结副本。

汇总 schema v2 保留 latest 与 append-only 两种口径；agreement 只使用同一 Run 内两个不同且非空 reviewer 身份的无序配对。同一人重复提交不会冒充独立复评。decision 返回 exact agreement 与 pooled symmetric-marginal kappa；六维返回总体/逐维 mean absolute difference、within-one rate 和 1–5 quadratic weighted kappa。无合格配对时为 `insufficient_data`/null，而不是虚构 0 一致性。

失败合同：LLM/协议失败的 step 为 `failed`，其 `meta.failure` 含 `outcome/error_code/retryable/http_status/retry_after_s/hint/provider/model/message/error_type`；断流时 `output` 保存已收文本，meta 标明 `partial_output_kept`。Run 为 `failed`，`result.failure/failures` 保存摘要，`result.snapshots.before/after` 和 `result.recovery` 给出 `partial_work_preserved`、恢复 snapshot 与可重试标志；对应后台 Job 也为 `failed`，`jobs.result.failure` 与 `job.failed` SSE 使用同一 diagnosis。Run 在持久化这些材料后才让 Job 失败，因此客户端应在 `job.failed` 后重新 GET run/document/timeline。`agent.run.failed` 同样携带 diagnosis。取消是 `cancelled`，不应显示为普通完成或模型失败。

## Skills

| 方法/路径 | 输入 | 返回/副作用 |
|---|---|---|
| GET `/api/skills` | project_id/role/enabled | visible skills + stats/dirs |
| POST `/api/skills/sync` | project_id | 写 registry cache |
| GET `/api/skills/{skill_id}` | project_id | parsed Skill |
| POST `/api/skills` | name/instructions/metadata/scope/overwrite | 写 SKILL.md + registry |
| POST `/api/skills/draft` | request/existing/project/model | 外部 LLM，返回 draft，未自动保存 |
| POST `/api/skills/{skill_id}/enabled` | query enabled | 写 state |
| DELETE `/api/skills/{skill_id}` | path | 写：builtin 拒绝 |
| POST `/api/skills/{skill_id}/copy` | new_id | 写 user copy |
| POST `/api/skills/import` | path/scope/project | 写 Skill dir |
| POST `/api/skills/preview` | skill_ids/role/project/budget | 返回注入文本/problems |
| POST `/api/skills/suggest` | text/role/project/limit | trigger/tag 推荐 |

## Export

| 方法/路径 | 输入 | 返回/副作用 |
|---|---|---|
| GET `/api/export/capabilities` | 无 | formats/tools/engines |
| POST `/api/export/convert` | text/direction/unicode_safe | Markdown↔LaTeX snippet |
| GET `/api/export/convert/capabilities` | 无 | converter 描述 |
| POST `/api/export/{project_id}` | format/language/citation/format options | 写 export path/warnings |
| POST `/api/export/{project_id}/pdf` | document_class/engine | 写 + subprocess，可能失败但保留 LaTeX |
| GET `/api/export/{project_id}/download` | path | 仅项目内 FileResponse |
| GET `/api/export/{project_id}/files` | 无 | exports listing |
| GET `/api/export/{project_id}/overleaf/status` | 无 | git/config/tool state |
| POST `/api/export/{project_id}/overleaf/zip` | class/language | 写 archive |
| POST `/api/export/{project_id}/overleaf/push` | class/language/message/force | 外部 Git 写 Overleaf |
| POST `/api/export/{project_id}/overleaf/pull` | apply_to_manuscript | 外部 fetch；apply 时覆盖手稿 |

静态 `/convert` 必须保持在动态 `/{project_id}` 之前注册；这是路由顺序合同。

## Versions

| 方法/路径 | 输入 | 返回/副作用 |
|---|---|---|
| GET `/api/versions/{project_id}` | limit | unified timeline/git/counts |
| GET `/api/versions/{project_id}/git/status` | path | 完整 Git status；untracked 枚举到实际文件 |
| POST `/api/versions/{project_id}/git/init` | 无 | 写 repo，并持久化 `project.git_enabled=true` |
| POST `/api/versions/{project_id}/git/commit` | message/paths/flush_first | 写 disk/Git |
| GET `/api/versions/{project_id}/git/log` | limit/path | commits |
| GET `/api/versions/{project_id}/git/diff` | ref/path/staged | diff/stat |
| GET `/api/versions/{project_id}/git/branches` | 无 | branches/current |
| POST `/api/versions/{project_id}/git/branch` | name/checkout | 写 branch/checkout |
| POST `/api/versions/{project_id}/git/checkout` | ref | 写工作树 + reindex，先 snapshot |
| GET `/api/versions/{project_id}/git/remotes` | 无 | remotes；保留空格路径，URL 凭据脱敏 |
| POST `/api/versions/{project_id}/git/remote` | name/url | 写 remote config；响应 URL 脱敏 |
| DELETE `/api/versions/{project_id}/git/remote` | name query | 仅移除 remote 配置；本地 commits、branches 和工作文件保持不变 |
| POST `/api/versions/{project_id}/git/fetch` | remote query | 外部读 remote 并更新 tracking refs；不修改本地手稿；返回 ahead/behind/diverged/ff 状态 |
| POST `/api/versions/{project_id}/git/pull` | remote query | 外部 fetch；仅干净树且可快进时写工作树；写前 DB snapshot + 磁盘手稿备份，写后 reindex；分叉返回 409 且不 merge |
| POST `/api/versions/{project_id}/git/push` | remote/branch query | 外部写 remote；无 force、无交互 prompt、non-fast-forward 返回错误 |
| POST `/api/versions/{project_id}/git/discard` | `confirm=true` | 写：先同步预检、DB snapshot、手稿副本和 binary Git patch，再丢弃并 reindex；untracked 保留 |
| GET `/api/versions/{project_id}/snapshots` | 无 | snapshot list |
| POST `/api/versions/{project_id}/snapshots` | label/kind | 写 snapshot |
| GET `/api/versions/{project_id}/snapshots/{snapshot_id}` | path | snapshot detail |
| DELETE `/api/versions/{project_id}/snapshots/{snapshot_id}` | path | 写：删除恢复点 |
| POST `/api/versions/{project_id}/snapshots/prune` | query keep_auto/older_days | 写：清理自动 snapshot |
| POST `/api/versions/{project_id}/save` | label/message/snapshot/git flags | 写 snapshot + commit |
| GET `/api/versions/{project_id}/compare` | left/right/language/context | unified diff |
| POST `/api/versions/{project_id}/restore` | ref/section_keys/take_snapshot | 写：恢复 DB/文件 |
| GET `/api/versions/{project_id}/sections/{section_key}/history` | limit | Git + snapshot history |
| GET `/api/versions/{project_id}/sections/{section_key}/at` | ref | 某版本章节文本 |

## 示例

```http
POST /api/search HTTP/1.1
Content-Type: application/json

{"mode":"idea","seed_text":"multi-agent evidence-grounded survey writing","project_id":"p1"}
```

```json
{"job_id":"<id>","mode":"idea"}
```

```http
POST /api/analysis/a1/place-idea
Content-Type: application/json

{"title":"My proposed method","abstract":"...","persist":true,"project_id":"p1"}
```

```http
POST /api/agents/run
Content-Type: application/json

{"project_id":"p1","pipeline":"section","section_keys":["related-work"],"skill_ids":["evidence-discipline"]}
```

精确 response schema 和 422 字段错误请使用当前 `/api/docs`；文档不复制自动生成的全量 JSON schema，以避免与代码漂移。

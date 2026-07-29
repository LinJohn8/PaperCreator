> 文档用途：学术检索系统与扩展方式  
> 最后检查：2026-07-28  
> 对应代码：`backend/papercreator/retrieval/`、`api/routes/search.py`  
> 文档状态：可用；结构化故障矩阵与 Electron 429 恢复已通过，live smoke 已执行但公网 SLA 未全绿，持续监控仍未完成

# 检索系统

## Provider

| ID | 来源 | Key | 特点 | 默认启用 |
|---|---|---|---|---|
| `arxiv` | arXiv | 无 | 预印本、摘要/PDF、严格 3.1s 限流 | 是 |
| `openalex` | OpenAlex | 可选 | 全学科、引用/参考文献/OA | 是 |
| `crossref` | Crossref | 无 | DOI 权威元数据，摘要稀疏 | 是 |
| `dblp` | DBLP | 无 | 计算机书目、无摘要/引用 | 否 |
| `doaj` | DOAJ | 无 | 审核 OA 期刊 | 否 |
| `europepmc` | Europe PMC | 无 | 生命科学、引用/OA | 否 |
| `pubmed` | PubMed | 可选 NCBI | 生医、MeSH/结构摘要 | 否 |
| `semanticscholar` | Semantic Scholar | 可选 | 语义检索/论文推荐；无 key 易 429 | 否 |
| `local` | Bib/RIS/CSV/JSON | 无 | 本地 Zotero/EndNote 导出 | 否，需 imports 目录 |

流程：规则/可选 LLM 查询展开 → Registry 排除未知 id、保留已知不可用源作结构化诊断 → `asyncio.gather` 并发 → Provider 自己限流/重试/缓存 → 后置过滤 → DOI/arXiv/title/作者去重合并 → reciprocal-rank fusion + 引用/年份等信号 → library/collection/history 持久化。

失败隔离按 Provider；缓存默认 168 小时。`idea`/`paper` 模式使用 seed text 展开；Semantic Scholar 支持更强语义推荐，但不是唯一途径。

## 故障状态机与恢复合同

`Provider.safe_search()` 永不把单源异常抛给 pipeline，而是始终返回 `ProviderStats`。稳定 `outcome` 为：`success`、`unavailable`、`rate_limited`、`timeout`、`authentication_error`、`http_error`、`network_error`、`invalid_response`、`provider_error`、`unexpected_error`。每条统计还可带 `error_code`、`retryable`、`http_status`、`retry_after_s` 和可行动 `hint`。HTTP 429/5xx/坏 JSON、401/403、timeout、连接错误与 Provider parser 异常分别归类；UI 不应根据自由文本猜故障类型。

pipeline 的完成语义分三种：

1. 全部成功或零命中：正常记录请求、统计和结果。
2. 部分失败：保留成功来源的论文，增加 `partial provider failure` warning，同时持久化成功/失败 stats。
3. 全部失败或没有可用来源：仍生成 `search_id`（`persist=true` 时）、写入完整实际 request、空结果和诊断，再发布 `search.done`；不能在历史写入前提前返回。

`search.provider` SSE 提供 outcome/errorCode/retryable/httpStatus/retryAfterS/hint；Search 结果页以诊断卡展示失败。按钮“仅重试可恢复来源”从 `SearchResponse.request` 重建请求，只选择 `retryable=true` 的失败 Provider，并强制 `use_cache=false`；原历史保持不变，重试建立新历史。不可重试的认证/配置/解析器异常不会被该按钮盲目提交。

## 桌面输入与可复现合同

Search 页支持 `keyword`、`idea`、`paper` 三种入口。`paper` 不再要求手工复制摘要：进入该模式会载入当前项目文献库，选择论文后以“标题 + 摘要”填充仍可编辑的 seed。页面每次挂载都重新调用 Provider 能力接口，因此刚导入 `.bib/.ris/.csv/.json` 后 `local` 可立即从不可用变为可用，无需重启应用。默认可用源每个页面生命周期只初始化一次；用户主动清空选择不会被 effect 自动恢复，提交时再做“至少一个源”校验。

`SearchRequest.use_llm_expansion` 是持久化请求合同，不是仅供预览的 UI hint。异步 Job、同步搜索和历史 rerun 都从请求读取它；rerun 只按接口约定覆盖 `use_cache`，保留原搜索的展开策略、mode、seed、providers 和过滤条件。`SearchResponse.request` 是展开后的真实执行快照，也是定向恢复的来源。Search 历史 UI 显示 mode、query/seed、Provider、结果数、失败来源数、时间和规则展开标志；“重新检索”和定向恢复都创建新历史，不覆盖原记录。2026-07-28 Electron E2E 已验证 Local+OpenAlex 429 部分失败、单源无缓存恢复、已有论文检索三次执行均持久化，文献仍为 12 篇且应用重启后历史恢复。

OpenAlex 默认端点为 `https://api.openalex.org/works`。`PC_OPENALEX_ENDPOINT` 或保存的 `retrieval.openalex_endpoint` 可指向 OpenAlex-compatible 镜像/代理；远端必须 HTTPS，HTTP 只允许 `127.0.0.1`、`localhost` 或 `::1`，避免可选 API key 经明文网络发送。末尾 `/` 会标准化移除；search、resolve 和 citation expansion 使用同一端点。

显式 `npm run test:backend -- --live` 于 2026-07-28 最新结果为 315 passed/1 failed：唯一失败是 arXiv 公共端点 timeout，单独重试后又明确 rate-limited；产品分别返回 `timeout`/`rate_limited` 并隔离单源失败。这证明故障合同生效，不能证明公共 API SLA 全绿；早先同日的全通过时点记录仅保留在历史修改记录中。

新增 Provider 必须声明 metadata/capabilities/rate limit，实现 search/resolve（若支持），在 registry 注册，并补解析 fixture、HTTP/异常分类、部分/全失败历史和选择回退测试。不得声称“搜索所有 GitHub 算法”：当前算法来自仓库实现，尚没有对 GitHub 算法库的自动发现/插件导入。

> 文档用途：一次检索的完整数据与异常路径  
> 最后检查：2026-07-28  
> 对应代码：`api/routes/search.py`、`retrieval/pipeline.py`

# 检索流程

桌面入口先在页面挂载时刷新 `/api/search/providers`；这使导入本地文献后才出现的 Local Provider 无需重启即可使用。`keyword` 直接输入 query；`idea` 可由当前项目 Idea 预填；`paper` 会载入当前项目文献库，用户选择论文后把标题与摘要写入可编辑 seed。页面允许用户清空默认源，但提交前要求至少选择一个。

后端支持异步 `/api/search` 和同步测试/小请求 `/api/search/sync`。验证 mode：keyword 要 query；idea/paper 要 seed_text/query。idea/paper 先规则提取短语/缩写，可选 LLM 生成有限查询变体。`use_llm_expansion` 存在于 `SearchRequest`，从 UI → SearchBody → 后台 Job → pipeline → history 全程保留；不能只在预览阶段生效。

Registry 解析启用/显式来源并只过滤未知 id；已知但缺 key/本地文件的 Provider 保留到 `safe_search()`，立即形成结构化 `unavailable` 而不做 I/O。其余 Provider 在共享 HttpClient 上按自己的 interval/concurrency/retry/search variant 上限并发。结果先后置过滤，再按强标识/标题/作者/年份去重；合并保留最好元数据及用户字段；RRF 和信号产生可解释 ranking components。

每个 Provider 完成后发送 `search.provider`，包含 outcome、机器错误码、HTTP/retry-after、可重试性和建议。结果随后 upsert library、关联 project/collection、记录 search/results/stats。单源错误不回滚其他源；即使所有源失败或没有可用源，也先写入空结果历史（`persist=true`）再发送唯一 `search.done`，因此后台 Job、SSE 和历史都能到达终态。零命中与失败通过 stats/outcome 区分，不能只看 `papers=[]`。

Search 页在任务完成后刷新历史并显示失败来源数。结果诊断卡使用结构化字段，不解析 error 文本；定向恢复从响应的实际 `request` 复制所有 mode/seed/filter/expanded queries，只保留 `retryable=true` 的失败源并强制关闭 cache。普通 rerun 同样创建新历史，但可重跑所有历史 Provider；外部数据可能变化，两者都不是结果集严格复现。

异常分支：Provider 不可用时卡片说明原因；零选择在客户端阻止提交；429/timeout/5xx/network/invalid response 可恢复，401/403 和配置缺失要求用户修正，unexpected parser error 指向日志；不存在的历史返回 404；历史参数无法解析时 rerun 返回明确错误；LLM 未配置或展开失败时 pipeline 降级到规则查询。修改 `SearchRequest` 或 `ProviderStats` 时必须同步检查 `SearchBody.to_request()`、历史 JSON、SSE payload、rerun/定向恢复、前端类型与 E2E。

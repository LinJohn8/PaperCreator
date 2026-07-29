> 文档用途：LLM Provider、协议、模型解析、流式和用量边界  
> 最后检查：2026-07-28  
> 对应代码：`backend/papercreator/llm/`、`core/config.py`  
> 文档状态：实验性

# LLM 系统

支持四类 wire protocol：`openai`（也覆盖 DeepSeek/OpenRouter/vLLM/LM Studio 等兼容服务）、`anthropic`、`gemini`、`ollama`。模型标识统一为 `provider:model`；角色 `chat`、`fast`、`embedding` 可各自映射默认模型，空配置会选择第一个可用 Provider。

`llm/backends.py` 处理认证、消息/工具/流式格式、模型列表、错误与重试；`llm/client.py` 提供统一 generate/stream/JSON 修复和 usage 记录。Provider 有 timeout、max_retries 和输入/输出每百万 token 价格，仅用于台账，不自动做成本路由。流式 read timeout 现在严格使用 Provider 配置，不再暗中放大到 600 秒；只有尚未收到 delta 的连接可自动重试，收到部分文本后不会重放，以免重复写作。

## 故障与恢复合同

所有 LLM 边界统一保存 `outcome/error_code/retryable/http_status/retry_after_s/hint/provider/model`。稳定 outcome 包括 `unavailable`、`configuration_error`、`rate_limited`、`authentication_error`、`timeout`、`network_error`、`http_error`、`invalid_response`、`stream_interrupted`、`empty_response`、`output_truncated`、`model_error` 和 `unexpected_error`。429 解析数字 `Retry-After`；401/403 不重试；5xx、timeout、network 和中途断流可重试；坏协议/坏 JSON 不被静默吞掉。

OpenAI stream 必须有 `[DONE]`，Anthropic 必须有 `message_stop`，Gemini 必须有 finish reason，Ollama 必须有 `done=true`。EOF、坏 SSE/NDJSON 或缺失 terminal event 都不会伪造成功 Completion；已经收到的文本、字符数与估算 token 会作为失败 step 的 partial output 保存。`llm_usage` 对每次实际调用只写一行：transport、非预期异常、embedding 与 stream 失败均记 `ok=false`；HTTP 成功但 JSON 合同解析失败会把同一行改为失败，不会留下伪成功或重复调用行。设置页 test 返回相同机器诊断和建议。

密钥可来自环境或 UI 写入 `secrets.json`。API 返回 `***set***`，日志有 token/key/URL 参数脱敏。Ollama 不需 key，但“已配置”不等于 endpoint 在线，设置页提供 test/probe。

限制：本轮没有真实云 key 端到端执行完整 Agent；默认模型名是配置初值，不代表 2026 年供应商的最新推荐；确定性 MockTransport 和本机 OpenAI-compatible 断流只证明协议、审计和恢复机制，不代表真实模型质量、费用或供应商 SLA。模型行为非确定，snapshot/git/引用检查仍是必要防线。新增协议必须实现统一 backend 合同、terminal 检查、流式取消、错误映射、usage 和故障测试。

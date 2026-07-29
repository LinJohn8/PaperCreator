> 文档用途：记录外部 API、模型服务、云服务和本机 CLI 依赖  
> 最后检查：2026-07-28  
> 对应代码：`retrieval/providers/`、`llm/backends.py`、`analysis/embeddings.py`、`convert/`、`vcs/`  
> 文档状态：按服务分别可用/实验性

# 外部服务

| 服务 | 用途 | 认证 | 超时/重试/降级 |
|---|---|---|---|
| arXiv/OpenAlex/Crossref/DBLP/DOAJ/Europe PMC/PubMed/Semantic Scholar | 元数据检索/resolve/OA | 多数无 key；部分可选 key | HTTPX timeout、每源限流/重试/cache；结构化 outcome；单源失败隔离、全失败仍写诊断历史 |
| OpenAI-compatible/Anthropic/Gemini | Agent、查询扩展、Skill 草稿/embedding | API key | 统一 failure contract；首个 delta 前才允许安全重试；严格 terminal event；失败 usage、partial output 与 retry/restore 证据持久化 |
| Ollama | 本地 LLM | 无 | endpoint probe；不在线为 configuration/unavailable；NDJSON 必须以 `done=true` 终止 |
| Hugging Face 或 mirror | sentence-transformer 模型 | 通常无 | 可 offline；失败退 TF-IDF/hashing |
| Overleaf | ZIP 上传或 Git Bridge | ZIP 无；Git URL/token | Git timeout、禁 prompt；ZIP 为免费降级 |
| Git CLI | 项目版本/Remote Git/Overleaf | Credential Manager/SSH/Overleaf token | scoped cwd、timeout、禁 prompt/force；项目 Pull 仅 clean ff-only 并保留恢复材料；分叉交给外部客户端；无 Git 时 snapshot 继续 |
| Pandoc | 可选 DOCX | 无 | 失败退内置 DOCX |
| TeX engines/bibtex | PDF | 无 | 缺失时保留 LaTeX project 并解释 |
| MyMemory | 可选中英全文翻译 | 本项目无需 key；公共服务；用户必须显式确认外发 | 文本离开本机；同步≤10k，Job≤100k 字符/250 请求；句段分块、限速、Retry-After/取消；只生成完整预览，确认前不写手稿；敏感文本应改用离线术语或自有本地模型 |

学术 API 的覆盖、免费额度和合同可能变化；Provider metadata 是当前代码声明，不是服务 SLA。OpenAlex 可通过 `PC_OPENALEX_ENDPOINT` 使用兼容镜像；远端只允许 HTTPS，明文 HTTP 仅限回环开发服务。密钥变量和配置位置见 [../reference/environment_variables.md](../reference/environment_variables.md)。

MyMemory 只作为明确标记的公共免费翻译选项，不是离线服务。PaperCreator 不应把未告知的手稿自动发送给它；用户选择 Provider 并发起翻译后才调用。敏感/未发表论文优先使用离线术语表或用户控制的本地 Ollama。其免费额度、数据条款和响应质量属于外部状态；当前没有自动 fallback 到公共服务。

## LLM 失败与重试边界

`llm/backends.py` 将 HTTP、timeout/network、协议、空响应、输出截断和模型错误映射到稳定 outcome，并保留 `error_code`、`retryable`、HTTP/retry-after、provider/model 与用户 hint。OpenAI-compatible 流必须看到 `[DONE]`，Anthropic 必须看到 `message_stop`，Gemini 必须给出 finish reason，Ollama 必须给出 `done=true`；坏 SSE/NDJSON 或提前 EOF 均不能记为成功。

自动重试只允许发生在尚未接收正文 delta 时；首个 delta 后的故障保存 partial output 并终止，避免重放造成重复文本。Run/Step/Job/SSE 使用相同 diagnosis，桌面可按原 request 重试或比较/恢复 pre-run snapshot。所有调用（含失败、embedding 与 JSON parse retry）进入 durable usage 和 token budget。真实云端质量、计费、长期限流与 SLA 尚未验收，确定性本机 HTTP fixture 只证明系统合同。

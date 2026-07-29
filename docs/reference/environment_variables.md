> 文档用途：当前代码读取的环境变量  
> 最后检查：2026-07-28  
> 对应代码：`.env.example`、`core/config.py`、`core/paths.py`、`electron/main.cjs`

# 环境变量

| 变量 | 默认 | 使用者 | 说明 |
|---|---|---|---|
| `PAPERCREATOR_WORKBENCH` | desktop 选择；dev 仓库根 | Electron/paths | 普通文件夹；home 为其 `.papercreator` 子目录 |
| `PAPERCREATOR_HOME` | 未显式时由 workbench 推导 | paths | 旧 headless/test override；优先于 WORKBENCH |
| `PAPERCREATOR_WORKSPACE` | `<home>/projects` | paths | 旧 headless/test 项目根 override，禁止误删 |
| `PC_HOST` / `PC_PORT` | 127.0.0.1 / 8765 | backend/Electron | API；Electron只读取 port |
| `PC_LOG_LEVEL` | INFO | logging | DEBUG/INFO/WARNING/ERROR |
| `PC_CORS_EXTRA` | 空 | API | 逗号分隔 origin；不等于认证 |
| `PC_CONTACT_EMAIL` | 空 | scholarly APIs | User-Agent/mailto，推荐 |
| `PC_OPENALEX_API_KEY` | 空 | OpenAlex | 可选额度提升 |
| `PC_OPENALEX_ENDPOINT` | `https://api.openalex.org/works` | OpenAlex | OpenAlex-compatible 镜像/代理；远端必须 HTTPS，HTTP 仅允许 localhost/127.0.0.1/::1 |
| `PC_S2_API_KEY` | 空 | Semantic Scholar | 可选，强烈建议 |
| `PC_NCBI_API_KEY` | 空 | PubMed | 可选限流提升 |
| `PC_CORE_API_KEY` | 空 | config only | Provider 尚未接入 |
| `PC_SPRINGER_API_KEY` | 空 | config only | Provider 尚未接入 |
| `PC_IEEE_API_KEY` | 空 | config only | Provider 尚未接入 |
| `PC_SCOPUS_API_KEY` | 空 | config only | Provider 尚未接入 |
| `PC_OPENAI_API_KEY` | 空 | LLM | 自动注册 OpenAI |
| `PC_OPENAI_BASE_URL` | OpenAI v1 | LLM | OpenAI-compatible override |
| `PC_ANTHROPIC_API_KEY` / `BASE_URL` | 空 / Anthropic | LLM | Anthropic |
| `PC_GEMINI_API_KEY` / `PC_GEMINI_BASE_URL` | 空 / Gemini API | LLM | Gemini；base 由动态变量名读取 |
| `PC_DEEPSEEK_API_KEY` / `PC_DEEPSEEK_BASE_URL` | 空 / DeepSeek v1 | LLM | OpenAI-compatible |
| `PC_OPENROUTER_API_KEY` / `PC_OPENROUTER_BASE_URL` | 空 / OpenRouter v1 | LLM | OpenAI-compatible |
| `PC_OLLAMA_BASE_URL` | example 为 localhost:11434 | LLM | 有值即注册本地 Provider |
| `PC_OLLAMA_MODEL` | 空 | LLM | Ollama 默认模型 |
| `PC_HF_ENDPOINT` / `HF_ENDPOINT` | huggingface.co | analysis | 模型 mirror；PC 优先 |
| `PC_OFFLINE_MODELS` | false | analysis/tests | 禁网络模型下载 |
| `PC_OVERLEAF_GIT_URL` | 空 | overleaf | secret-adjacent 项目 URL |
| `PC_OVERLEAF_GIT_TOKEN` | 空 | overleaf | 密钥，不记录 |
| `PC_PYTHON` | 自动探测 | Electron | 指定解释器完整路径 |
| `PC_DESKTOP_SHUTDOWN_TOKEN` | 每次桌面启动随机生成 | Electron→owned backend | 内部 256-bit 关机 capability；不供用户配置、不进入 Renderer/OpenAPI/日志 |

`PC_CORE/SPRINGER/IEEE/SCOPUS_API_KEY` 目前只有配置字段，没有对应 Provider 实现，状态为“存在配置但未调用”。不得在文档或测试输出中填真实值。

安装态 Electron 设置 `PAPERCREATOR_WORKBENCH` 并直接启动 bundled exe，忽略 `PC_PYTHON`。PyInstaller 冻结态不会向上寻找源码仓库 `.env`；这是避免安装包继承开发密钥的安全合同。

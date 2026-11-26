# 配置

## 路径与工作台优先级

桌面安装态由 Electron 设置 `PAPERCREATOR_WORKBENCH=<用户选择的普通文件夹>`，后端把 home 固定为 `<workbench>/.papercreator`，project root 固定为 `<home>/projects`。开发态未显式设置时，仓库根被视为工作台，因此数据位于仓库 `.papercreator/`。

`PAPERCREATOR_HOME`、`PAPERCREATOR_WORKSPACE` 只为测试/旧 headless 调用保留，且 `HOME` 的优先级高于 `WORKBENCH`。正常桌面产品不要混用这些变量。PyInstaller 冻结态不会向上查找源码仓库，也不会误读源码 `.env`。

## 实际加载顺序

默认模型 → `<home>/config/settings.json` → `<home>/config/secrets.json` → `.env` → process environment。后层覆盖前层；因此 UI 保存值是本机持久默认，启动器/部署环境可以强制最终值。`.env` 只填 process environment 中尚不存在的变量，手工编辑后调用 `POST /api/settings/reload` 会刷新此前由 `.env` 注入且未被进程改写的值。

普通字段写 settings，Provider/API/Overleaf secret 写 secrets；API 返回掩码。空字符串可以清除普通可空字段，secret 使用专用删除接口。`GET /api/settings/sources` 只返回优先级、文件路径/存在性、字段名和环境变量名，不返回任何配置值或密钥，可用于解释某个 UI 保存值为何被环境覆盖。

## 核心配置

| 项 | 默认 | 用途 | 风险 |
|---|---|---|---|
| server.host/port | `127.0.0.1:8765` | 本地 API | 非回环且无鉴权为高风险 |
| server.log_level | INFO | 日志 | DEBUG 可能暴露更多研究元数据 |
| retrieval.enabled_providers | arxiv/openalex/crossref | 默认检索源 | 全空被 API 拒绝 |
| retrieval limits | 50/source，300 total | 规模/时间 | 大值受限流、内存和费用影响 |
| cache TTL | 168h | 学术响应缓存 | 过长可能陈旧 |
| retrieval.openalex_endpoint | `https://api.openalex.org/works` | OpenAlex-compatible 镜像/代理 | 远端只允许 HTTPS；HTTP 仅回环地址，避免 key 明文外发 |
| analysis.embedding_backend | auto | 向量方案 | 模型变化会改变地图 |
| analysis reducer/clusterer | auto | 3D/聚类 | 重跑可不可比 |
| analysis heatmap_grid | 40 | 网格精度 | 高值增加 payload/CPU |
| llm temperature/tokens | 0.4 / 4096 | 生成 | 质量、费用和截断 |
| llm run_token_budget | 400000 | 单 run 硬上限 | 应按项目/模型降级 |
| writing bilingual | true | 配对语言 | 增加 Agent 调用 |
| writing auto_git_commit | true | 自动版本 | 仅在项目 Git 可用时 |
| writing latex_engine | pdflatex | 编译 | 中文会切 XeLaTeX |
| UI | dark、zh-CN、13px、sidebar 300 | Renderer | 部分 UI 状态当前未持久化完整 |
| `PAPERCREATOR_WORKBENCH` | Electron 选择 | 单根工作台 | 路径必须存在且可写；切换需重启 |
| `PC_DESKTOP_SHUTDOWN_TOKEN` | Electron 每次启动随机值 | owned backend 优雅退出 | 内部 capability，不写 `.env`/settings，不暴露给 Renderer；不能设为固定产品密钥 |

环境变量完整表见下文。路径只通过 `core/paths.py` 解析，不要在模块内硬编码。


## 环境变量

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

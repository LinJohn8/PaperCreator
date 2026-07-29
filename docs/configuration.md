> 文档用途：配置层、默认值、写入位置与修改风险  
> 最后检查：2026-07-28  
> 对应代码：`core/config.py`、`.env.example`、`api/routes/settings.py`  
> 文档状态：基于当前实现

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

环境变量完整表见 [reference/environment_variables.md](reference/environment_variables.md)。路径只通过 `core/paths.py` 解析，不要在模块内硬编码。

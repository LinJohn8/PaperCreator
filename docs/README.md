> 文档用途：PaperCreator 项目级 LLM Wiki 入口与导航  
> 最后检查：2026-07-29  
> 对应代码：整个仓库  
> 文档状态：基于当前代码、构建与测试结果整理

# PaperCreator 项目 Wiki

PaperCreator 是一个 Windows 优先、本地运行的多 Agent 论文工作台：从学术检索、文献库、3D 研究图谱和缺口分析，到分阶段/全自动写作、双语手稿、导出和本地版本控制。

这套 Wiki 只描述软件项目本身。它供开发者、代码 Agent、测试与部署人员使用，不是用户或 Agent 实例的独立知识库。

## 当前快照

| 项目 | 结论 |
|---|---|
| 版本 | `0.1.0`，首个开发可运行版本 |
| 产品形态 | Electron 桌面工作台，VS Code 式多面板；首次启动选择工作台 |
| 数据合同 | 用户所选文件夹中的 `.papercreator/` 是唯一托管根；项目与分类资料都在其中 |
| 后端验证 | 2026-07-28 当前 360 collected，非联网 `357 passed, 3 deselected in 28.76s`；3 项 live 检索本轮未重复；快速开始设置、DB v6、逐章节合并、条件 OCR、复杂 DOCX、CLI 与 OpenAPI route snapshot 均进入全量 |
| 前端验证 | Renderer/E2E TypeScript 与 95-module Vite build 通过；Playwright 真实 Electron 长链 `1 passed (1.6m)`，新增快速开始/菜单/日志/焦点/两档截图，并保持单语言、项目恢复、助手、翻译、合并、Git/导出、退出保存、checkpoint 与四次启动覆盖；独立短场景仍是 P1 |
| 开发启动 | `npm run setup`，然后 `npm run dev` |
| 发布状态 | `部分可用`：旧自包含 NSIS 的安装/升级/卸载证据有效，但本轮按用户要求暂缓重新打包；当前源码为 157 paths/181 operations，不能声称已经进入旧安装器；正式发布仍缺 clean VM 和代码签名 |
| Git 状态 | 2026-07-29 初始化 `main` 并发布至 `LinJohn8/PaperCreator`；此前没有可恢复的提交历史 |
| 数据 | 开发态为仓库 `.papercreator/`；安装态为所选工作台 `<folder>/.papercreator/`；禁止删除或纳入源码提交 |

完整模块状态见 [current_status.md](current_status.md)，本轮证据见 [changelog_internal.md](changelog_internal.md)。

## 推荐阅读顺序

新开发者：

```text
project_overview.md → architecture.md → directory_structure.md
→ module_map.md → runtime_flow.md → development_guide.md
```

代码 Agent：

```text
llm_context.md → current_status.md → architecture.md
→ module_map.md → tasks/pending_tasks.md → known_issues.md
```

部署与维护人员：

```text
deployment.md → configuration.md → reference/data_files.md
→ systems/logging_system.md → troubleshooting.md
```

项目负责人：

```text
current_status.md → changelog_internal.md → tasks/priority_tasks.md
→ roadmap.md → technical_debt.md
```

## 文档导航

### 核心

| 文档 | 用途 |
|---|---|
| [project_overview.md](project_overview.md) | 定位、用户、范围与产品决策 |
| [current_status.md](current_status.md) | 已实现、受限、未实现的真实快照 |
| [architecture.md](architecture.md) | 进程、模块、数据与部署架构 |
| [directory_structure.md](directory_structure.md) | 真实目录、生成物与保护边界 |
| [module_map.md](module_map.md) | 修改入口、上下游与风险 |
| [runtime_flow.md](runtime_flow.md) | 启动、请求、后台任务和退出 |
| [data_flow.md](data_flow.md) | 数据来源、加工、保存与可恢复性 |
| [systems/workbench_storage.md](systems/workbench_storage.md) | `.papercreator` 工作台合同、分类和备份边界 |
| [api_reference.md](api_reference.md) | 当前实际 HTTP API 清单 |
| [configuration.md](configuration.md) | 环境、JSON 设置与功能开关 |
| [deployment.md](deployment.md) | 开发运行、打包现状、更新与备份 |
| [development_guide.md](development_guide.md) | 开发约定与扩展方法 |
| [testing_guide.md](testing_guide.md) | 测试命令、覆盖与缺口 |
| [troubleshooting.md](troubleshooting.md) | 可复现故障排查 |
| [known_issues.md](known_issues.md) | 已确认问题 |
| [technical_debt.md](technical_debt.md) | 工程债务 |
| [roadmap.md](roadmap.md) | 分阶段路线 |
| [llm_context.md](llm_context.md) | 后续代码 Agent 必读上下文 |

### 专项与任务

- [systems/](systems/)：前端、后端、检索、分析、LLM/Agent、Skill、写作导出、存储、日志、安全和外部服务。
- [flows/](flows/)：工作台选择/导入、启动、请求、检索、分析、Agent、读写、导出和更新流程。
- [reference/](reference/)：文件、环境变量、数据文件、端口、状态、术语和依赖索引。
- [tasks/](tasks/)：已完成、待完成、优先级、未来与迁移任务。

## 文档维护规则

1. 代码与文档冲突时，以已验证的当前代码为准，并记录差异。
2. 只使用 [reference/status_definitions.md](reference/status_definitions.md) 定义的状态；规划不得写成已实现。
3. 不记录密钥、token、私人论文内容或 `.papercreator/` 内的用户数据。
4. 代码修改后至少检查 `current_status.md`、`changelog_internal.md`、对应系统/流程文档、任务文档、`known_issues.md` 和 `llm_context.md`。
5. API、配置、数据结构或持久化路径变化必须同步更新参考文档，并说明迁移/回滚。
6. 提交 Wiki 修改前运行 `npm run validate:docs`；它机械检查全部 `docs/**/*.md` 的本地链接、空文档、围栏、编码替换字符和高置信密钥特征，但不能替代代码语义复核。
6. 最近检查日期代表实际重新核对代码或运行结果的日期，不是机械更新时间。

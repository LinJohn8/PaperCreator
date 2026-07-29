# PaperCreator

[![CI](https://github.com/LinJohn8/PaperCreator/actions/workflows/ci.yml/badge.svg)](https://github.com/LinJohn8/PaperCreator/actions/workflows/ci.yml)

> [!WARNING]
> **这是一个实验性半成品，当前处于低优先级维护状态。**
>
> 它不是稳定产品，也不保证持续开发、向后兼容、生成内容正确性或论文质量。请勿把它直接用于重要论文、投稿或不可恢复的数据。使用前请阅读下方限制，并自行备份工作台。

PaperCreator 是一个 Windows 优先、本地运行的论文研究工作台原型。项目尝试把多源学术检索、文献库、研究图谱、双语手稿、LLM Agent、本地版本控制和多格式导出放进一个 Electron 桌面应用。

这个仓库开源的主要目的，是保留已有实现、供学习和实验，并让有兴趣的人可以继续改进。当前维护者不承诺路线图、响应时间或正式发行周期。

## 当前状态

已有实现包括：

- Electron + React 桌面工作台，以及本地 FastAPI 后端
- arXiv、OpenAlex、Crossref 等多源检索和本地文献库
- 基于嵌入/降维/聚类的 3D 研究图谱与启发式缺口候选
- Markdown 章节编辑、双语文本、PDF/DOCX/TeX 导入
- OpenAI-compatible、Anthropic、Gemini、Ollama 等 LLM 接口
- 多 Agent 写作实验、提示词模板和项目 Skill
- 项目快照、本地 Git，以及显式启用的远程 Git 操作
- Markdown、DOCX、LaTeX、BibTeX 和 Overleaf ZIP 导出
- 空工作台快速开始、故障诊断和本地日志

但它仍然是半成品，主要缺口包括：

- 没有经过真实专家评审证明的生成质量；AI 内容可能幻觉、错引或遗漏关键工作
- 没有稳定 API、数据迁移承诺或长期兼容策略
- 没有在干净 Windows 10/11、标准用户、杀毒软件矩阵中完成发布验收
- 安装包未签名；仓库中的旧构建产物不代表当前源码
- OCR、PDF 编译、真实 Overleaf/GitHub/GitLab 认证依赖外部环境，覆盖不完整
- 公共学术 API 可能限流、超时或改变字段
- 未完成大规模文献库、超大目录、网络盘和断电恢复压力测试
- 缺少完整的组件级可访问性、视觉回归和多平台测试

更细的实现状态和已知问题见 [docs/current_status.md](docs/current_status.md) 与 [docs/known_issues.md](docs/known_issues.md)。

## 风险与数据边界

- 默认数据保存在你选择的工作台目录下的 `.papercreator/` 中。停止应用后备份整个目录，才能保留数据库、项目、配置和历史。
- LLM、MyMemory、学术检索和远程 Git 功能可能把请求内容发送给第三方。请先确认数据许可、隐私政策和费用。
- API key 可以写入工作台设置。不要提交真实 `.env`、`secrets.json`、数据库或工作台目录。
- Agent 输出和研究缺口只是辅助信息，不是事实结论。引用、实验、许可和最终稿必须由人复核。
- 本项目按 MIT License 原样提供，不附带任何担保。

## 本地开发

环境要求：

- Windows 10/11（主要开发目标）
- Node.js 20+
- Python 3.10+

```powershell
git clone https://github.com/LinJohn8/PaperCreator.git
cd PaperCreator
npm run setup
npm run dev
```

配置项示例位于 [.env.example](.env.example)。所有配置均为可选；不配置 LLM 时，Agent 功能不可用，但本地项目、编辑和部分检索功能仍可运行。

常用验证命令：

```powershell
npm run test:backend
npm run typecheck
npm run build
npm run test:e2e
npm run validate:docs
```

最近一次本地验证记录为：后端非联网测试 `357 passed, 3 deselected`，TypeScript 和生产构建通过，真实 Electron 主链 `1 passed`。这些结果只说明当前测试环境中的合同成立，不代表真实模型质量、公共服务可用性或正式发布质量。

## 工作台结构

首次启动会要求选择一个普通目录，并在其中创建 `.papercreator/`：

```text
ResearchWorkbench/.papercreator/
├─ projects/                    论文项目
├─ library/ideas/               研究 Idea
├─ library/reference-papers/    参考论文
├─ library/own-papers/          自己的论文和旧稿
├─ library/code-projects/       研究代码
├─ library/datasets/            数据集
├─ library/supplementary/       补充材料
└─ library/inbox/               待分类材料
```

运行时数据不会被纳入本仓库。不要把个人工作台复制到源码目录后强制提交。

## 参与项目

Issue 和 Pull Request 可以提交，但不保证及时回复或合并。开始较大改动前，请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [docs/llm_context.md](docs/llm_context.md)，并在说明中明确测试范围、数据迁移影响与未验证部分。

安全问题请按 [SECURITY.md](SECURITY.md) 处理，不要在公开 Issue 中粘贴密钥、未公开论文或工作台数据。

## License

[MIT](LICENSE)

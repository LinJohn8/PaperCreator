> 文档用途：说明 PaperCreator 的产品定位、边界与长期方向  
> 最后检查：2026-07-27  
> 对应代码：`apps/desktop/`、`backend/papercreator/`  
> 文档状态：基于当前代码整理

# 项目概览

## 定位与核心价值

PaperCreator 将原本分散在学术数据库、网页 AI、笔记、Overleaf、Word 和 Git 中的论文工作流收进一个本地工作台。核心价值不是“让模型一次生成一篇文章”，而是把检索证据、空间分析、分角色写作、引用检查和可回滚修改连成可审计流程。

主要用户是需要长期处理论文项目的研究者，以及协助其开发和维护工作台的代码 Agent。当前阶段为 `开发中` 的 Windows 优先单机应用，不包含账户、多租户、云同步或团队权限系统。

## 产品形态决定

选择“桌面软件为主、Web 能力为辅”，继续采用 Electron，而不是退回普通网页：

- 长时间后台检索、分析和 Agent 运行需要稳定的本地进程与系统通知入口。
- 文件导入、项目目录、Git、LaTeX/Pandoc、Overleaf Git 和本地模型都是桌面环境的天然能力。
- 论文工作要求同时查看目录、文献、图谱、编辑器、日志与运行状态，VS Code 式多面板比单页表单更合适。
- FastAPI 仍保持独立 HTTP 接口，未来可以增加浏览器客户端或远程计算节点，而不用改写核心业务。

当前 UI 已落实为 Activity Bar + 上下文 Sidebar + 主工作区 + Output Panel + Status Bar + Command Palette，工作流顺序为：项目 → 检索 → 文献库 → 图谱 → 手稿 → Agents → 版本 → 导出。

## 工作台与分类决定

产品名定为 PaperCreator。首次启动选择一个普通文件夹，软件只在其中创建 `.papercreator/`；项目、资料、数据库、配置、密钥、日志、缓存、模型、Skill 和 Electron 浏览器数据都归入这一目录。这样 Windows 安装目录可以独立升级/卸载，用户复制一个目录即可移动或备份工作台。

“新论文”与输入资料不是同一概念：

- `projects/`：每项是一篇正在写的新论文，包含手稿、集合、分析、导出和默认本地 Git（创建时可关闭）；远端 Git 始终另行显式添加。
- `library/ideas/`：尚未形成论文的研究问题与贡献假设。
- `reference-papers/` 与 `own-papers/`：分别保存他人论文和自己的既有成果/旧稿。
- `code-projects/`、`datasets/`、`supplementary/`：分别保存实现、数据和图表/协议。
- `inbox/`：临时落点，不应长期替代正式分类。

导入默认复制托管副本，防止源文件移动后工作台失效。代码目录复制排除依赖树、虚拟环境、构建产物和 `.env` 密钥。

## 已覆盖的使用场景

- 从关键词、研究 idea 或已有论文标识/内容生成检索并合并多来源结果。
- 管理全局文献库、项目集合、标签、重复项、用户 idea/自有论文。
- 将论文嵌入、降维、聚类为 3D 图谱，显示关键词、热力、图关系与候选缺口。
- 将 idea 或 paper 增量定位到已有图谱，并移除分析点而不删除文献。
- 选择 LLM Provider，执行全自动、分章节、拼接或自定义多 Agent 流水线。
- 在本地 Markdown 主稿和中文配对文本间编辑，生成引用与文献表。
- 使用离线术语、公共 MyMemory 或已配置 LLM 生成短文/长文/批量译文；长文先形成可取消、可恢复的完整预览，确认后一次写入。
- 使用项目感知 AI 对话，跨重启恢复线程；按工作台/项目统计、导出和预览后清理对话数据，建议动作仍需二次确认。
- 导出 Markdown、LaTeX、DOCX、BibTeX、ZIP；可生成 Overleaf ZIP，配置后使用 Git Bridge。
- 使用数据库快照和项目级 Git 查看、比较、保存与恢复版本。
- 从内置、用户和项目目录加载 `SKILL.md`，也可由 LLM 生成草稿。

## 技术栈

| 层 | 技术 |
|---|---|
| 桌面壳 | Electron 32、Node.js 20+ |
| 前端 | React 18、TypeScript 5.6、Vite 5、Zustand、CodeMirror 6、Three.js |
| 后端 | Python 3.10+、FastAPI、Uvicorn、Pydantic、HTTPX |
| 分析 | NumPy、scikit-learn；可选 UMAP、sentence-transformers |
| 持久化 | 单根 `.papercreator`、SQLite schema v6 + FTS5、项目/分类托管文件、提取 sidecar、提示词、助手对话/导入来源映射、Git |
| Windows 打包 | PyInstaller onedir 后端、electron-builder/NSIS |
| 测试 | pytest、pytest-asyncio、FastAPI TestClient、Playwright Electron、真实 NSIS 安装器 smoke；仍缺组件测试、CI 和 clean VM |

## 当前明确不负责

- 自动保证论文事实正确、学术原创性、投稿合规或研究伦理；最终责任仍由作者承担。
- 付费数据库全文绕过、版权规避或未授权抓取。
- 云端账户、多人实时协作、权限管理、远程队列和托管数据库。
- 实验代码执行、数据采集与统计结果自动复现（当前重点是文献和写作工作台）。

## 长期方向

在可分发 Windows 版本和真实端到端验证稳定后，再考虑浏览器伴侣端、可插拔检索 Provider SDK、论文图谱算法插件、引用事实核验、实验资产管理和协作同步。未进入代码的方向均为建议项，详见 [tasks/future_tasks.md](tasks/future_tasks.md)。

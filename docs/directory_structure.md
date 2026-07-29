> 文档用途：记录真实目录结构、入口、生成物与保护边界  
> 最后检查：2026-07-28  
> 对应代码：仓库根目录  
> 文档状态：基于当前目录扫描

# 目录结构

```text
PaperCreator/
├─ apps/desktop/                 Electron + React 桌面端
│  ├─ electron/                 main.cjs、preload.cjs（高风险信任边界）
│  ├─ assets/brand/             可审计 SVG 品牌母版与生成的 Windows PNG
│  ├─ scripts/                  品牌渲染与 afterPack EXE 资源写入
│  ├─ src/
│  │  ├─ api/                   HTTP、SSE、类型和端点封装
│  │  ├─ components/            工作台壳、编辑器、3D、日志等
│  │  ├─ state/store.ts         Zustand 全局状态与跨视图动作
│  │  ├─ styles/                主题和布局
│  │  └─ views/                 10 个业务视图
│  ├─ dist/                     Vite 构建产物，可重新生成
│  ├─ release/                  NSIS 与 win-unpacked，可重新生成
│  └─ package.json              前端依赖、extraResources 和 NSIS 配置
├─ backend/
│  ├─ papercreator/
│  │  ├─ api/                   FastAPI 工厂与 14 组业务路由
│  │  ├─ core/                  配置、路径、数据库、作业、事件、日志、模型
│  │  ├─ store/                 领域存储层
│  │  ├─ retrieval/providers/   9 个检索 Provider
│  │  ├─ analysis/              嵌入/降维/聚类/关键词/热力/缺口/图
│  │  ├─ llm/                   4 类 LLM 协议适配
│  │  ├─ agents/                11 角色和 4 流水线
│  │  ├─ writing/               模板、手稿、引用、翻译分块/Provider
│  │  ├─ convert/               Markdown/LaTeX/DOCX/PDF/Overleaf
│  │  ├─ skills/                SKILL.md 模型、加载和注入
│  │  ├─ vcs/                   Git 与快照统一版本
│  │  └─ resources/skills/      4 个内置 Skill，只读来源
│  ├─ tests/                    当前 360 项 pytest（含 3 项 live）
│  └─ pyproject.toml            Python 包、依赖、pytest 配置
├─ docs/                        本项目 Wiki，必须随代码维护
├─ scripts/                     setup/backend/test、installer E2E 与 PyInstaller 构建入口
├─ build/                       后端 runtime/打包中间物，可重建
├─ .papercreator/               开发工作台系统目录，禁止读取私人内容/删除/提交
├─ .venv/                       本地 Python 环境，可重建
├─ node_modules/                Node 依赖，可重建
├─ .env                         本地配置/密钥，禁止提交
├─ .env.example                 无密钥模板
├─ package.json                 工作区入口与统一命令
└─ package-lock.json            Node 锁文件，必须保留
```

## 用户所选工作台

~~~text
<用户选择的普通文件夹>/
└─ .papercreator/                       唯一系统目录；整体可移动/备份
   ├─ workbench.json                    工作台格式标记，当前 schema v1
   ├─ papercreator.db[-wal/-shm]        SQLite，当前 DB schema v6
   ├─ projects/<slug>/                  一篇新论文一个目录
   ├─ library/
   │  ├─ ideas/                         Idea Markdown
   │  ├─ reference-papers/pdfs/         他人论文及 OA PDF
   │  ├─ own-papers/                    自己论文、旧稿
   │  ├─ code-projects/                 研究代码托管副本
   │  ├─ datasets/                      数据集
   │  ├─ supplementary/                 图表、协议等
   │  ├─ inbox/                         待分类材料
   │  └─ */.partial-res_<16hex>         目录导入期间的保留 staging；成功 rename，失败/取消清理
   ├─ config/settings.json              非密设置
   ├─ config/secrets.json               API key/token
   ├─ logs/papercreator.log             后端主日志
   ├─ logs/errors.log                   后端错误日志
   ├─ logs/desktop.log                  Electron/后端生命周期日志
   ├─ cache/http/ 与 cache/embeddings/  可重建缓存
   ├─ electron/                         Chromium userData/cache/local storage
   ├─ models/                           可重下的本地模型
   ├─ skills/                           用户 Skill
   ├─ exports/                          非项目临时导出
   └─ backups/                          预留备份目录
~~~

## 保护与生命周期

| 路径 | 类型 | Git | 可删除 | 部署/备份 |
|---|---|---|---|---|
| `.papercreator/papercreator.db` | 用户状态 | 忽略 | 否 | 必须备份；SQLite 主文件及 WAL/SHM 一起处理 |
| `.papercreator/projects/` | 新论文手稿项目 | 根仓库忽略；子项目可独立 Git | 否 | 最高优先级备份/迁移 |
| `.papercreator/projects/<slug>/.papercreator/manuscript-sync.json` | schema v2 手稿整体/逐节 DB/磁盘 fingerprint 基线 | 子项目 Git 应忽略 | 可重建但分叉时不得盲建；v1 只保守兼容 | 跟项目一起备份 |
| `.papercreator/projects/<slug>/.papercreator/conflicts/` | 强制选边/Git discard 恢复副本与 patch | 子项目 Git 应忽略 | 否 | 人工确认无用前不得清理 |
| `.papercreator/library/` | Idea/论文/代码/数据等托管输入 | 忽略 | 否 | 与数据库一起备份 |
| `.papercreator/library/*/.partial-res_<16hex>` | 运行中/异常中断的目录导入 staging | 忽略 | 仅严格名称且确认无活跃 Job 后可清；启动会自动回收 stale 项 | 不备份；不要把相似用户目录当 staging |
| `.papercreator/config/secrets.json` | 密钥 | 忽略 | 删除会丢配置 | 不进入普通日志/源码包 |
| `.papercreator/cache/` | 缓存 | 忽略 | 是（服务停止后） | 可重建 |
| `.papercreator/models/` | 模型权重 | 忽略 | 是但重下代价大 | 可选共享/备份 |
| `.papercreator/library/reference-papers/pdfs/` | OA PDF | 忽略 | 视用户需求 | 可能可重下，但不保证 |
| `.papercreator/electron/` | 当前工作台的浏览器状态 | 忽略 | 可重建但会丢 UI 状态 | 通常无需单独恢复 |
| `.venv/`、`node_modules/` | 依赖 | 忽略 | 是 | 用锁文件/pyproject 重建 |
| `apps/desktop/dist/` | 构建产物 | 忽略 | 是 | `npm run build` 重建 |
| `apps/desktop/release/`、`build/` | 安装包/后端构建物 | 忽略 | 是 | `npm run package` 重建 |
| `apps/desktop/assets/brand/icon.svg`、`icon.png` | 品牌源与确定性构建输入 | 应提交 | PNG 可由 SVG 重建 | `npm run brand:build`；修改母版后必须重打安装器 |
| `backend/**/__pycache__`、`.pytest_cache` | 缓存 | 忽略 | 是 | 不部署 |
| `docs/`、源码、lock/pyproject | 源码 | 应提交 | 否 | 发布和维护必需 |

## 当前异常

- 根目录已于 2026-07-29 初始化 Git，并使用 `main` 与 GitHub remote；此前源码没有可恢复的提交历史。
- `.papercreator/` 已存在真实开发运行数据；文档只确认路径和类别，不读取/记录私人内容。
- 根 `dist/win-unpacked/` 是曾从错误工作目录运行 electron-builder 产生的约 901 MB ASAR 构建副本；不被 package 使用且已被 `.gitignore` 忽略。自动递归删除被执行策略拒绝，需用户手工确认后删除；正式产物只在 `apps/desktop/release/`。
- 仓库已有 MIT License 和 Windows GitHub Actions CI；仍没有 Docker/Compose，桌面目标保持 Windows。

> 文档用途：开发环境、扩展方式、代码和文档约束  
> 最后检查：2026-07-28

# 开发指南

## 准备与命令

要求 Windows、Node 20+、Python 3.10+、Git。运行 `npm run setup`；修改前读 [llm_context.md](llm_context.md) 和目标系统/流程文档。

```powershell
npm run dev
npm run backend -- --dev
npm run typecheck
npm run test:e2e
npm run build
npm run package
npm run test:backend
npm run test:backend -- --live
```

live 命令会访问真实学术服务，不应在每次离线测试隐式执行。`test:e2e` 使用真实 Electron/本地后端但隔离临时工作台，不访问 live Provider。

## 扩展清单

- 新 API：route Pydantic model → store/domain function → error contract → TestClient test → frontend endpoint/type/view → API/Wiki。
- 新 Provider：Provider subclass + metadata/capabilities/rate-limit + registry + parser fixture + live smoke（必要时）+ retrieval docs。
- 新分析算法：明确输入空间、高维/3D语义、随机种子、回退、保存状态、增量 transform、兼容旧分析和 caveat。
- 新 LLM：backend contract、stream、model list、usage、timeout/retry、secret scrub 和设置 UI。
- 新 Agent：角色 requires/output schema、prompt preview、预算/取消、snapshot、partial writes 和引用纪律。
- 新 Skill：优先用 SKILL.md，不把任意执行代码伪装成 Skill。
- 新数据字段：migration + model/store/API/TS/export + backup/rollback。
- 新工作台类别/导入：Paths + `WorkbenchResourceKind` + DB migration/store/API/TS/Panel + secret/path/large-copy tests + 存储/迁移文档。
- 新页面：ActivityBar/`renderView`/ViewId、Sidebar、store、loading/error/empty/locale、build。

## 约束

每次修改 Wiki 后运行：

```powershell
npm run validate:docs
```

该命令只读取 `docs/**/*.md`，检查空文档、本地链接、代码围栏、UTF-8 replacement 字符和高置信密钥特征；它不会读取 `.env` 或用户 `.papercreator/`。语义状态、路径职责和代码合同仍需开发者结合实际代码复核。

Python 路径统一由 `core/paths.py`，配置由 `core/config.py`；所有产品数据只能在所选 `.papercreator`，新论文与分类输入保持分离；外部进程要 timeout/禁 prompt/脱敏；后台循环要 cancel checkpoint；SQLite 写入用现有 helpers/transaction；不手改用户运行数据。

打包修改至少验证 `npm run brand:build` 的 PNG 尺寸/alpha、应用与安装器提取图标、EXE ProductName/version、普通权限空 builder cache、dev Python、bundled `--check`、`resourcesPath/backend`、ASAR、窗口关闭后端口释放、工作台含空格/中文和卸载保留数据。保持 `win.signAndEditExecutable=false` 并由 `after-pack.cjs` 写资源；不要让安装态回退系统 Python或读取源码 `.env`。

当前没有正式 formatter/linter 配置和分支/提交规范，不能虚构。建议后续引入 Ruff/Prettier/ESLint、Conventional Commits 和 CI。在根 Git 建立前，任何大改风险更高。

完成后更新 `current_status`、changelog、系统/流程、API/配置（如适用）、任务/风险和 `llm_context`。

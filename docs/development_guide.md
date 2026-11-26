# 开发指南

## 环境与启动

主要开发目标为 Windows 10/11，需要 Node.js 20+、Python 3.10+ 和 Git。从仓库根执行：

```powershell
npm run setup
npm run dev
```

仅启动后端可用 `npm run backend -- --dev`。设置示例见 [`.env.example`](../.env.example)；未配置模型时仍可使用本地项目、编辑和部分检索功能。工作台位置和环境覆盖规则见[配置指南](configuration.md)。

## 验证

| 命令 | 用途 |
|---|---|
| `npm run test:backend` | 隔离临时工作台的后端离线测试 |
| `npm run test:backend -- --live` | 显式访问公共学术服务的测试 |
| `npm run typecheck` | TypeScript 检查 |
| `npm run build` | Renderer 生产构建 |
| `npm run test:e2e` | Playwright 驱动 Electron 和真实本地后端 |
| `npm run validate:docs` | 文档链接、围栏、编码和高置信密钥特征检查 |
| `npm run package` | Windows 后端与桌面安装包构建 |
| `npm run test:installer` | 临时目录内的安装、覆盖安装及卸载检查 |
| `npm run test:release` | 打包后执行安装器检查 |

按改动范围选择验证。E2E 的外部 LLM 使用本地确定性响应；测试通过不代表真实模型质量、公共服务可用性或专家评审结果。安装器检查发现已有 PaperCreator 安装时会拒绝运行。发布还需确认干净 Windows 环境、标准用户权限和外部工具依赖。

## 扩展约束

新增接口要同时更新 Pydantic 模型、领域或 store 层、错误合同、前端类型及 [API 参考](api_reference.md)。数据格式变化需要迁移、备份及恢复方案。

路径只通过 `core/paths.py` 获取；数据写入当前工作台。手稿写入遵守同步指纹和快照规则，Git checkout 后执行 reindex，普通编辑提交前 flush。后台任务包含取消检查点；SQLite 写入使用既有事务设施。

新增 Provider 或 LLM 协议要覆盖限流、超时、取消、错误映射及密钥脱敏。Agent 修改应验证预算、失败后的部分结果、恢复快照和引用一致性。研究图谱及缺口输出属于辅助信息。

## Windows 打包

构建链使用 PyInstaller onedir runtime 和 electron-builder NSIS；安装态从 `resourcesPath/backend` 启动随包后端，不能回退到系统 Python 或加载源码 `.env`。品牌资源使用 `npm run brand:build`；`win.signAndEditExecutable=false` 配合项目 `after-pack.cjs` 写入 EXE 资源。

打包改动需检查图标、版本元数据、bundled backend、含空格和中文的路径、窗口关闭后端口释放，以及覆盖安装和卸载后工作台数据保留。停止应用后备份工作台，再测试升级。

## 文档与贡献

文档描述接口行为、配置、架构和可复现操作步骤。示例使用相对路径、`~` 或占位符；不要提交个人目录、会话内容、内部任务清单、逐轮开发记录或原始审计输出。运行数据、密钥、未公开论文和生成安装包不纳入源码。

贡献规则见 [CONTRIBUTING.md](../CONTRIBUTING.md)。提交说明列出实际执行的验证及仍依赖外部服务或人工检查的部分。

> 文档用途：开发运行、打包现状、更新、停止、备份和恢复  
> 最后检查：2026-07-28  
> 对应代码：`scripts/`、`apps/desktop/package.json`  
> 文档状态：开发运行可用；Windows 自包含安装链路部分验收

# 部署与运行

## Windows 本地开发

```powershell
npm run setup
npm run dev
```

`setup` 创建 `.venv`、安装 Python extras/测试、安装 Node 依赖、从模板创建忽略的 `.env` 并运行诊断。网络/torch 冲突可使用：

```powershell
npm run setup -- --no-analysis
```

仅后端：`npm run backend`；诊断：`cd backend; ..\.venv\Scripts\python.exe -m papercreator --check`。仅 Web UI：先启动后端，再 `npm run dev:web --workspace @papercreator/desktop`。

## 构建与打包

```powershell
npm run build
npm run package
npm run test:installer
```

`package` 会先执行 `brand:build`，从 `assets/brand/icon.svg` 重建 PNG；随后构建 Renderer/冻结后端，最后由 `after-pack.cjs` 写入 Windows EXE 资源并生成 NSIS。保持 `win.signAndEditExecutable=false`：项目钩子已经负责 icon/metadata；重新启用 electron-builder 内置编辑会在未启用 Developer Mode 的 Windows 上尝试解压 winCodeSign 的 macOS symlink 并失败。代码签名是后续独立步骤，不等同于 executable resource editing。

`build` 运行 TypeScript 检查与 Vite。`package` 的真实顺序是：Vite build → `scripts/build-backend.mjs` → PyInstaller onedir → electron-builder → NSIS。生产 Electron 只启动 `resources/backend/papercreator-backend.exe`，不会回退到系统 Python。

当前产物：

- `apps/desktop/release/PaperCreator-Setup-0.1.0.exe`，本轮 135,381,021 bytes，SHA-256 `2A4C36AA4FFBF59316FF9DA91016C330D68BF8E7E500B3CE8062DF7E7B931E43`。
- `apps/desktop/release/win-unpacked/`，用于不安装 smoke。
- `build/backend-runtime/papercreator-backend/`，608 个文件、153,894,049 bytes。

本机 `npm run test:installer` 已自动完成：在中文/空格临时路径静默安装 → 启动真实已安装 EXE/bundled backend → 创建 Idea/新论文项目 → 同路径覆盖安装 → 重启确认数据恢复 → 静默卸载 → 确认工作台数据保留与安装注册清除。脚本发现当前用户已有 PaperCreator 安装时会拒绝覆盖，且只清理经过严格前缀检查的系统临时根。PaperCreator 品牌图标、ProductName、FileDescription 和版本已写入应用 EXE，安装器提取图标与应用一致；构建还在空 electron-builder cache、无 Developer Mode 符号链接权限条件下通过。尚未在完全干净 Win10/11 VM 验证且未签名，因此状态仍为“部分可用”，不是正式发行。

上一版冻结后端 `--check` 返回 `no problems found`，合同为 134 paths / 154 operations，并通过已安装 EXE、同路径升级和卸载保留工作台 E2E。本轮按用户要求暂不打包；当前源码已增至 157 paths / 181 operations，DB v6 助手归档/脱敏、OCR、逐章节合并、CLI endpoint 和本轮其他新增实现尚未进入旧 NSIS，发布时必须重新 build backend/package/installer smoke。源码桌面继续使用 owned backend 优雅 shutdown/WAL checkpoint；clean VM/重新打包仍需复验。

## Linux/Docker

后端源码设计为跨平台，但仓库没有 Dockerfile、Compose、systemd 或 Linux 部署脚本；状态为尚未实现。Electron 当前产品决策为 Windows 优先。

## 更新语义

| 变化 | 操作 |
|---|---|
| 仅后端源码（dev editable） | 重启 backend/Electron |
| 前端源码 | dev 热更新；生产需 `npm run build` |
| package-lock | `npm install` + build |
| pyproject | `npm run setup` + backend tests |
| Electron/build 配置 | 重打安装包 |
| Python 后端代码/依赖 | 重建 PyInstaller runtime 并重打安装包 |
| DB schema | 先备份，运行迁移，再验证 stats/data |

重启不等于重建；重建不应删除 `.papercreator`；清缓存不应删 DB/projects/library；不存在 Docker volume，文档不得给出虚构 Compose 命令。

## 工作台、升级和卸载边界

安装目录只保存应用二进制。首次启动选择 `<folder>` 后，所有实质数据写入 `<folder>/.papercreator/`；AppData 只保留工作台定位指针。覆盖安装不迁移工作台，卸载器也不得删除它。选择新工作台只改变下一次启动位置，不会搬运旧数据。

推荐升级：关闭 PaperCreator → 备份完整 `.papercreator/` → 运行新安装器覆盖原安装目录 → 启动并确认 `/api/system/health` 中 DB schema/项目/资源计数 → 再继续工作。

## 状态、停止与恢复

- 状态：`GET /api/system/health`、UI StatusBar、Output、logs。
- 停止：正常关闭 Electron；独立后端用 Ctrl+C。不要在写入中强杀。
- 备份：停止应用，复制所选工作台中的整个 `.papercreator/`；它已经包含 DB、projects、library、配置、Skill 和浏览器态。敏感 `secrets.json` 需按密钥介质保护。
- 恢复：把完整 `.papercreator/` 放入新的普通文件夹，安装态选择其父文件夹，再运行 bundled backend `--check` 或查看 health；不要只恢复 projects 而遗漏 DB/library。
- 回滚源码：当前无根 Git，暂不能提供可靠源码回滚；项目手稿用 Versions/Git/snapshots。

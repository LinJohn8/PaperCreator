> 文档用途：本地信任边界、认证现状与敏感操作  
> 最后检查：2026-07-28  
> 对应代码：`api/app.py`、`electron/preload.cjs`、`core/logging_setup.py`、`vcs/`  
> 文档状态：本地单用户可用；网络暴露不安全

# 认证与安全

当前没有用户账户、session 或通用 API 权限系统。安全假设是 FastAPI 仅绑定 `127.0.0.1`，由同一 Windows 用户的 Electron 访问。唯一窄 capability 是内部关机：Electron 每次 owned backend 启动随机生成 256-bit token，仅放入子进程环境和关机请求头；Renderer/preload/OpenAPI/日志均不暴露，错误或缺失值返回 404。它不是用户认证，不能据此把 `PC_HOST` 默认改为 `0.0.0.0`；远程模式仍必须增加认证、CSRF/Origin/权限审计。

已实现防线：Renderer context isolation + nodeIntegration off；preload 只暴露指定 IPC，标题栏菜单 IPC 仅接受 File/Edit/View/Help 并钳制坐标；外链只允许 HTTP(S)；下载/删除/Git cwd 做 projects containment；资源路径必须解析到 `.papercreator`，文件删除进一步限制在 `library/`；禁止把工作台/祖先目录复制进自身；代码导入排除 `.env*`、依赖和构建目录；目录根 link/reparse 拒绝、嵌套 link/reparse/特殊文件不跟随，源身份前后复核，并以同盘 staging→原子 rename→DB 最后登记；破坏性 API 要 confirm；Git 默认本地、禁自动 push/prompt/force/自动 merge，remote URL、命令和错误脱敏，Pull 只允许 clean fast-forward 且先做 snapshot/手稿备份，remove remote 保留本地历史；密钥分文件、API 掩码、日志脱敏；CORS 限本地/配置来源。

已知边界：Electron sandbox 当前为 false；`secrets.json` 不是 OS Credential Manager；本地恶意进程仍可访问回环 API和用户文件；项目/用户 Skill 能影响 prompt；导入文件和论文文本是不可信内容；当前 link/reparse 安全合同已有受控测试，但普通权限真实 junction、网络盘、超长路径、杀软占用和真实大规模矩阵仍未验证；Markdown/LaTeX 输出需防止打开时的外部行为。Git remote URL 的内嵌凭据会从 PaperCreator API/诊断脱敏，但 Git 仍可能把原值保存在项目 `.git/config`，应优先使用系统 credential helper 或 SSH。安装器未代码签名。

若引入远程 Web：必须先完成认证授权、TLS、CSRF、速率限制、文件访问租户隔离、Secret Vault、审计日志和 threat model，不能仅修改 host。

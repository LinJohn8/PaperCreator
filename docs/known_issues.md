> 文档用途：已确认问题与临时/根本方案  
> 最后检查：2026-07-29

# 已知问题

| 问题 | 严重度 | 影响 | 临时方案 | 根本方案 | 状态 |
|---|---|---|---|---|---|
| 自包含 NSIS 尚未做 clean-VM/代码签名验收 | 高 | SmartScreen、杀软或缺失系统组件风险未知；正式图标已完成 | 使用已验证本机安装包；保留源码启动 | Win10/11 x64 干净 VM 矩阵、Authenticode 代码签名 | 部分可用 |
| 真实 Provider/模型完整写作未验收 | 高 | 质量、实际费用与供应商漂移未知 | 已有 quality v2、不可变正文/摘要、Rubric v3 双 fingerprint、盲评/分析包、独立复评 kappa/MAD 基础设施；确定性 OpenAI-compatible HTTP/JSON/SSE 验证正常与断流合同 | 用公开固定论文集和真实专家执行双盲评，预注册 rubric/阈值，比较 Provider/模型/费用；基础设施通过不等于金集已经建立 | 实验性 |
| 本机盲评不是完整双盲试验平台 | 中 | Reviewer 可从工作台其他页面或文件系统推断模型身份，缺少随机任务分配和防串通 | 全屏模式视觉隐藏外层身份；blind JSON 删除 run/project/model/pipeline/reviewer/既有决定和本地 PDF path并带 fingerprint | 增加独立样本随机化/分配、reviewer pseudonym 策略、封存后揭盲流程和跨机器导入；由专家流程验收 | 开发中 |
| Live 检索只有单次证据、无持续监控 | 中 | 公共 API 后续字段/限流变化可能无法及时发现 | 2026-07-28 最新显式 live 为 315 passed/1 arXiv timeout，单独重试为 rate-limited；离线已有结构化失败分类与 Electron 429 恢复合同 | 受控 nightly/provider contract 监控与趋势告警 | 部分可用 |
| Electron E2E 覆盖仍集中于一条主链 | 中–高 | 真实模型/真实远端认证与分叉解决/PDF 与真实 Overleaf Git/首次原生 dialog 回归仍可能漏检 | 主链已覆盖 OpenAlex 429 和 LLM stream interruption 的 deterministic 故障/恢复、六类导出和本地 bare remote，另有 installer E2E 与后端安全合同；真实外部环境仍手工 smoke | 拆分场景，增加逐个 Provider 的低频 live 层、真实模型/GitHub/GitLab/PDF/Overleaf 专项环境和 clean-VM installer 矩阵并接入 CI | 部分可用 |
| Windows 外部扫描器可延迟释放 E2E 临时 DB | 低（仅开发测试） | 应用已完成 shutdown/checkpoint/退出后，Defender/索引器仍可能让系统 Temp 的最终 DB 在清理窗口内返回 EBUSY；失败调试运行会留下小型 `papercreator-e2e-*` 目录 | E2E 对严格 temp/prefix 路径有界退避；关闭超时只回收该测试 owned Electron PID tree；`npm run cleanup:e2e` 默认 dry-run，并要求最小年龄，显式 `--apply` 才删除 | CI 定期运行有界清理并保留 JSON 报告；评估杀软排除仅限 CI 临时根 | 已有安全工具，宿主锁仍可能发生 |
| 新增桌面功能尚未形成独立短 E2E 场景 | 中 | 当前长链已覆盖快速开始/菜单/日志/焦点/截图、语言持久化、章节 CRUD、提示词、助手归档/脱敏、翻译、逐章节合并、本地 Git、退出自动保存与优雅关机，但失败定位成本仍高 | 后端 357 项非联网合同覆盖核心安全；TypeScript/生产构建与真实 Electron `1 passed (1.6m)` | 拆分 onboarding、locale/search settings、writing、assistant/prompts/local Git 短场景 | 开发中 |
| MyMemory 公共服务隐私、额度和 SLA | 中 | 用户明确选择并确认后文本发送给公共第三方；服务仍可能限额、停机或改变行为 | 默认不选公共服务；敏感文本用离线术语或自有本地模型；Job 失败/取消不写手稿 | 已实现≤100k/250 请求、句段稳定分块、限速、Retry-After/timeout/network 重试、取消、完整预览和一次确认写入；后续只做真实低频 SLA 趋势与可替换 Provider | 部分可用 |
| 当前开发机没有真实 OCR 引擎/渲染器 | 中（条件能力） | 产品已能探测并调用本地 Tesseract，但本机只能显示缺失能力，不能 live 证明中英扫描识别质量 | 保留原 PDF；未安装时返回 `requires_ocr` 预览；文字层页面不做 OCR | 在隔离环境安装 Tesseract + 中英语言包和 pypdfium2/pdftoppm，用公开扫描 corpus 验收准确率、超时与页限 | 代码/合同可用，live 待外部能力 |
| 投稿模板包依赖用户授权与人工验收 | 中 | 不同 venue 许可证/文件结构/编译环境不同，错误使用会导致版权或投稿格式问题 | 只导入用户提供且确认有权使用的 ZIP；安全解压并保存来源/许可证/SHA-256 | venue profile、TeX 编译沙箱、许可证/版本更新提示和 golden fixtures；不批量镜像 Gallery | 部分可用 |
| 离线 Hashing 图谱只表达词形重叠 | 中 | 可稳定增量定位，但同义表达/深层语义可能距离失真 | UI 显示 `hashing:256` 和算法说明；重要研究改用 sentence-transformers/LLM embedding | 增加可下载本地语义模型、质量基准和模型选择向导 | 部分可用 |
| API 无鉴权 | 高（仅网络暴露时） | 非回环会允许本机/网络调用 | 保持 127.0.0.1 | 远程模式前加认证/授权/TLS | 部分可用 |
| `secrets.json` 非 OS Vault | 中 | 同用户恶意进程可读 | 文件权限/不共享 home | Windows Credential Manager/加密 vault | 尚未实现 |
| 同一章节双改仍需人工选边 | 低–中 | sync manifest v2 已自动合并不同章节修改，但同节双改、增删/重命名和旧 v1 基线仍保守阻塞 | 横幅显示两侧修改章节并显式选边；合并/覆盖前保存 DB snapshot 与两侧恢复副本 | 如真实需求成立，schema v3 增加共同祖先内容和显式三方 diff；不自动猜测 | 部分可用 |
| Remote Git 不自动解决分叉 | 中 | Versions UI 已有 remote/Fetch/ff-only Pull/Push，但 diverged history 会 409；URL 内嵌凭据仍会由 Git 存在项目 `.git/config` | 使用系统 Credential Manager/SSH；分叉在外部 Git 客户端显式解决后再 Fetch；PaperCreator API 脱敏且绝不 force/自动 merge | 增加真实 GitHub/GitLab 认证矩阵、分叉可视化与可恢复的显式 merge/选边设计 | 部分可用 |
| `manuscript/full.md|tex` 是派生预览 | 中 | 外部直接编辑不会被 reindex，后续 flush 会重建 | 只外部编辑 `NN-key.md|tex`；重要 full 修改先另存 | 在文件头明确生成属性，或设计可逆 combined parser/独立保护 | 部分可用 |
| 目录导入真实极限规模与 Windows 文件系统矩阵未验证 | 中 | 真实 10GB、百万小文件、网络盘、超长路径、低空间卷、普通权限 junction/reparse 或杀软占用下的吞吐/取消延迟未知 | 当前可取消 Job、空间预检、link/reparse 排除、源变化检测、原子提交和启动清理已通过受控合同与 65-file Electron 链；大任务从 Output → Jobs 观察 | 增加生成式大规模/网络盘/长路径/低空间/权限/异常重启基准并定义支持阈值 | 部分可用 |
| 工作台切换不自动搬迁数据 | 低 | 用户可能误以为旧数据被复制 | UI 已明确“旧工作台不移动/删除” | 增加受控迁移/复制向导和校验 | 部分可用 |
| 导出未逐 venue 人工验收 | 中 | 排版/公式/参考文献偏差 | 使用用户导入的官方包后预览并人工修订 | fixture + PDF/Word goldens + venue profile 验收 | 部分可用 |
| Docker/Linux 部署不存在 | 低（当前 Windows） | 无服务器部署路径 | 直接 Python 启动 | 需求确认后实现 | 尚未实现 |
| CORE/Springer/IEEE/Scopus key 有配置无 Provider | 低 | UI/模板易造成能力错觉 | 不展示为已支持 | 实现 Provider 或移除预留 | 尚未接入 |
| 根 `dist/` 有错误工作目录构建副本 | 低（磁盘） | 约 901 MB ASAR 浪费空间，易与正式 release 混淆 | 只使用 `apps/desktop/release` | 用户确认后删除根 `dist`；构建始终从 workspace script 运行 | 待清理 |

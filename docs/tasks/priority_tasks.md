> 文档用途：P0–P4 排序、依据和验收入口  
> 最后检查：2026-07-29

# 优先级任务

## P0

当前没有尚未修复且已确认必然导致服务不可用/数据丢失的开放缺陷。以下回归一旦出现立即按 P0：projects/library containment 失效、restore/delete 无恢复点、secret 泄漏、DB migration 破坏。依据：不可恢复或安全影响。

## P1

1. Windows 正式发行验收：自包含链路、正式图标和版本资源已实现，但 clean VM 与代码签名仍阻塞公开交付。
2. 真实 LLM Agent 质量/费用矩阵：quality v2、不可变正文、Rubric v3 双 fingerprint、blind/analysis packet 和复评 kappa/MAD 基础设施已完成；现在的阻塞是外部实验本身——公开金集选择、专家双盲执行、阈值校准、真实模型质量与费用，而不是再加一个前端 checkbox。
3. 扩展前端 Electron E2E：核心长链已覆盖语言重启持久化、检索恢复、章节 CRUD/双目标、提示词、助手确认动作、仅本地 Git 与优雅关机；下一步拆为可定位的 locale/settings、writing/import/translation、assistant/prompts/Git 短场景。真实模型、真实 GitHub/GitLab 认证/分叉解决、PDF/真实 Overleaf Git、首次原生 dialog 和 clean-VM 仍是发布缺口。

## P2

1. 目录导入真实规模与 Windows 文件系统矩阵：Job/进度/取消/空间/原子清理已完成，仍需真实 10GB、百万小文件、网络盘、长路径、低空间卷和普通权限 junction/reparse 基准。
2. Live Provider 定期合同/限流趋势监控和大库分析基准；确定性故障分类与 429 桌面恢复已完成。
3. DOCX/LaTeX/Overleaf 真实 venue 人工与 golden 验收。
4. Windows Credential Manager/Secret Vault 与 Electron sandbox 评估。
5. 图谱解释 UI：覆盖率、算法 backend、caveat 和不确定性更显著展示。
6. 手稿同节三方 diff/merge：sync manifest v2 和不同章节安全合并已完成；同一章节双改、增删/重命名、旧 v1 基线继续人工选边，无 CRDT。
7. OCR/复杂格式真实验收：本地 OCR adapter、能力探测和复杂 DOCX 表格/公式/脚注 exact golden 已完成；剩余是带真实 Tesseract 中英语言包的公开扫描 corpus、复杂 Word 人工核对和公共翻译低频 SLA。
8. Venue 模板验收：选取用户明确许可的代表性 publisher/会议包，建立不入库第三方文件的测试清单、编译沙箱和版本/许可证提示。
9. AI/Agent 敏感数据治理：对话 JSON/JSON.GZ 幂等恢复、保留批删和消息级不可逆脱敏已完成；剩余是另一个数据域中的 Agent prompt/output 保留与清理策略，不能隐式破坏质量证据。

## P3

拆分大文件/Store slices、Ruff/ESLint/Prettier、依赖 lock、OpenAPI 生成类型、DB/workbench/project 分层 manifest、性能 telemetry（本地/可关闭）。method/path route snapshot 已完成。优先级依据：提高开发效率，但不先于交付/数据安全。

## P4

算法插件 registry、Provider SDK、远程 worker、浏览器伴侣端、协作同步、自动发现 GitHub 算法实现。均为长期建议项，需先建立安全和兼容合同。

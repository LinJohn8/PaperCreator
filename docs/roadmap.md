> 文档用途：按风险和价值组织长期路线；未实现项不视为承诺  
> 最后检查：2026-07-28

# 路线图

## M0：可信开发基线（当前）

已完成：桌面/后端主骨架、单根 `.papercreator` 工作台与七类输入、原子目录导入、免费检索、文献/3D分析、LLM/Agent/Skill/写作/导出/版本、稳定引用键、quality v2、不可变正文、Rubric v3 双指纹、blind/analysis 包与复评一致性统计、手稿冲突保护；后端离线测试全绿；Windows 本机安装链和项目级 Wiki。

## M1：Windows 可交付闭环

PyInstaller+NSIS、本机安装/升级/卸载保留、正式品牌、根 Git/MIT/CI，以及 Electron E2E 的目录导入→检索/图谱/Idea→Agent/双语→冻结正文→blind packet→双人复评/agreement→无模型重启→同步/Git/Remote Git→六类导出链已完成；下一步仍是 clean VM/签名、真实专家金集与真实模型、真实远端/PDF/Overleaf和文件系统矩阵。

## M2：真实研究工作流质量

多 Provider live 稳定性 → 大规模去重/分析基准 → 用现有 blind packet 建立封存样本/专家双盲金集 → 用 Rubric v3 双 fingerprint 与 kappa/MAD 校准自动 gate → 真实 LLM 小节/全稿质量、成本和引用支撑率 → 目标模板/OCR/复杂 Word 人工验收 → 同节内容级三方 diff。不同章节的安全合并已在 M1 源码完成。

## M3：可扩展平台

建议项：稳定 Provider SDK、分析算法 registry/plugin contract、Skill provenance、可复用 workflow definitions、实验资产/数据集链接、模型路由策略。先有合同和沙箱，再宣称插件生态。

## M4：可选协作/远程

建议项：在 threat model、账户/授权、TLS、同步冲突、Secret Vault、审计和部署成熟后，增加浏览器伴侣端/远程 worker/协作；不直接把当前无鉴权 API 暴露出去。

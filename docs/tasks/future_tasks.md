> 文档用途：区分用户明确需求、代码可确认后续和建议项  
> 最后检查：2026-07-28

# 后续任务

## 用户明确提出

- Windows 优先软件式论文工作台。
- 安装后选择普通文件夹作为工作台；所有系统内容只放其 `.papercreator/`，并把新论文、Idea、参考论文、自己的论文、项目代码等分类解释清楚。
- 可自定义扩展的免费/多源检索，支持 idea/论文反向检索。
- 文献 3D 方位、关键词热力、算法分类、缺口识别、idea/paper 增删定位。
- 多 LLM 与一键全做/分部分做后连接。
- 文本、Overleaf、Word 等写作/转换。
- 对话/手工创建 Skill。
- 中英对照、版本对照、本地 Git commit。
- 长期维护项目级 LLM Wiki。

上述大多已有代码基线；“完成”的最终判定仍受 pending 的真实交付/质量验收约束。

## 从代码可确认

- 配置预留 CORE/Springer/IEEE/Scopus key，但 Provider 未实现。
- PyInstaller/NSIS、正式品牌资产、本机自动 installer 链与本地 Remote Git 安全闭环已验收；clean VM、代码签名、analysis extra、Overleaf Git、真实 GitHub/GitLab 认证与分叉解决仍缺完整真实环境验收。
- Agent 已有 Rubric v3 双 fingerprint、不可变正文、blind/analysis packet、accepted 后端硬门禁和复评 kappa/MAD；真实公开论文金集、专家双盲执行、自动信号校准和真实模型/费用矩阵仍未完成。
- 前端 endpoints 有少量 API 没有明显视图直达，可在 E2E/产品审查时决定保留或补 UI。

## 建议项

- 建议项：稳定的 Retrieval Provider SDK 和示例模板，而不只是复制现有类。
- 建议项：分析算法 registry + provenance（代码/论文/许可证/参数）后，再支持用户安装 GitHub 算法。
- 建议项：研究问题/证据矩阵、claim–citation graph 和引用原文定位。
- 建议项：在现有本机 blind packet 与 weighted kappa 基础上增加 reviewer pseudonym/任务随机分配、封存/揭盲状态、金集 manifest 导入和 ICC/置信区间；不能把多数票直接定义为事实真值。
- 建议项：Zotero 双向连接、OCR 质量基准与可替换引擎、PDF/Word 表格和图提取（需版权和本地隐私设计）；基础本地 Tesseract OCR 与 DOCX 公式/脚注线性提取已完成。
- 建议项：实验代码/数据集/结果资产与手稿 claim 关联。
- 建议项：协作/云同步；必须在认证、冲突和加密成熟之后。

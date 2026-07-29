> 文档用途：导出格式、外部工具和 Overleaf 关系  
> 最后检查：2026-07-28  
> 对应代码：`backend/papercreator/convert/`、`api/routes/export.py`  
> 文档状态：部分可用

# 导出系统

| 格式 | 实现 | 外部依赖 | 当前状态 |
|---|---|---|---|
| Markdown | 自研 assembly | 无 | 可用 |
| BibTeX | 自研 citation map | 无 | 可用 |
| LaTeX project | 自研转换/模板/sections/references | 无；编译另需 TeX | 可用 |
| DOCX | python-docx，失败/缺失时内置最小 OOXML | 无硬依赖；Pandoc 可选 | 可用，格式需人工验收 |
| ZIP | 项目导出打包 | 无 | 可用 |
| PDF | 先 LaTeX，再运行 pdflatex/xelatex/lualatex/bibtex | 本机 TeX | 当前机器不可用 |
| Overleaf ZIP | 生成可上传 archive | 无 | 可用 |
| Overleaf Git | clone/fetch/push/pull | Git、付费 Git Bridge、URL/token | 尚未真实端到端验证 |

中文检测会自动选择 XeLaTeX/ctex，避免 pdflatex 假成功。LaTeX→Markdown 明确有损。导出只写项目 `exports/`，下载 API 验证目标位于项目根内。

真实 Electron E2E 已从 Export UI 生成 Markdown、DOCX、LaTeX、cited-only BibTeX、bundle ZIP 和 Overleaf ZIP；读取磁盘核对正文、真实 `\cite{}`、匹配 bibliography、LaTeX 目录结构与两个 ZIP 的入口布局，并在后端/Electron 重启后核对导出历史。该测试发现并修复了“先生成 `\cite`、随后被 Markdown escaper 变为 `\textbackslash{}cite`”的问题：当前实现用内部占位符保护生成命令，转换后再恢复，并有后端专项回归。

外部命令设置 timeout、禁用 Git prompt，并脱敏 URL/token。投稿模板、图表、公式、脚注和复杂参考文献仍需按目标 venue 人工验收。

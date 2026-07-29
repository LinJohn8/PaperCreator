> 文档用途：导出、PDF 与 Overleaf 流程  
> 最后检查：2026-07-28  
> 对应代码：`convert/`、`api/routes/export.py`

# 导出流程

读取 project/document/sections/papers → assembly + citation key validation → 按格式 exporter → 写项目 `exports/` → 返回 path/warnings → Electron reveal/open 或受限 download。

LaTeX：先识别引用并暂存生成的 `\cite{}`，Markdown 子集转换完成后恢复命令，再写 main/sections/references/build scripts；不能把生成命令直接交给普通文本 escaper。CJK 选择 XeLaTeX。PDF 在此基础上运行 TeX/bibtex 多轮，缺工具时 LaTeX 项目仍保留。DOCX 优先可选 Pandoc/`python-docx`，否则内置 OOXML。

Overleaf ZIP 无需账户 API；Git Bridge push/pull 使用 scratch clone 并只管理 PaperCreator 拥有的文件。pull 默认只 fetch，不 apply；apply 有损且覆盖章节，必须显式选择并先快照。

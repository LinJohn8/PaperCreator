> 文档用途：运行与构建依赖、用途和替换风险  
> 最后检查：2026-07-28  
> 对应代码：`package-lock.json`、`backend/pyproject.toml`

# 依赖参考

## 开发运行要求

- Node.js `>=20`；Electron 32/React 18/TypeScript 5.6/Vite 5。
- Python `>=3.10`；本轮验证 3.11.5。
- FastAPI 0.115.6、Uvicorn 0.34、Pydantic 2.10.4、HTTPX 0.28.1。
- pypdf `>=5,<7`：提取带文字层的 PDF；扫描 PDF 默认不做隐式 OCR。
- NumPy `>=1.24,<3`、scikit-learn `>=1.3,<2`；本轮安装解析为 NumPy 2.4.6、scikit-learn 1.9.0。

终端 Windows 安装包不要求系统 Node/Python：Electron runtime 和 PyInstaller onedir Python 后端随安装器提供。它仍可能依赖 Windows 系统运行库，需 clean VM 验收。

## 可选

- OCR extra：Tesseract 可执行程序 + `pypdfium2`，或系统 `pdftoppm` 渲染器；仅在用户显式选择本地 OCR 时启用。语言必须存在于 Tesseract language data，逐页有超时和页数上限；当前开发机未安装，尚无真实 live 证据。

`analysis`：UMAP 0.5.x、sentence-transformers 2.x、transformers 4.x、huggingface_hub <1；版本上界用于兼容 Windows/现有 torch。无可选包时 TF-IDF/PCA/KMeans 仍工作。

`export`：python-docx；无它仍有内置 OOXML。外部 Git/Pandoc/TeX 通过 PATH 探测，不是 pip/npm 依赖。

`dev`：pytest、pytest-asyncio；Playwright Test 1.62.0（精确锁定）用于 Electron E2E。E2E 复用项目 Electron 32.2.0，不需要 Playwright 下载额外 Chromium；安装依赖时可使用 `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`。

`package`：PyInstaller `>=6.10,<7`；当前构建为 6.21.0。Node 构建使用 electron-builder 25.1.8、锁定的 `rcedit` 4.0.1 和 NSIS cache；后端 runtime 由 `scripts/build-backend.mjs` 生成后作为 `extraResources`。`rcedit` 仅在 Windows afterPack 写 icon/metadata，避免 electron-builder winCodeSign 压缩包的符号链接权限问题；升级它必须在 Node 20、普通 Windows 权限和空 builder cache 下验证。

升级风险：Pydantic/FastAPI schema、scikit-learn HDBSCAN/序列化、transformers/torch、Electron security defaults、Three/CodeMirror bundle。修改依赖必须更新锁文件、运行全套测试/构建，并验证已保存分析的兼容性。

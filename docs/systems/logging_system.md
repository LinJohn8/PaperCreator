> 文档用途：日志初始化、格式、脱敏、轮转与排错  
> 最后检查：2026-07-27  
> 对应代码：`core/logging_setup.py`、`api/routes/system.py`  
> 文档状态：可用

# 日志系统

`setup_logging(level, logs_dir)` 在 app factory/diagnostics 初始化。控制台用于 Electron 捕获；`papercreator.log` 记录主日志，`errors.log` 记录错误。格式含时间、级别、logger 和消息；后端 Output panel 可读取尾部，Electron 另保留最多 2000 行子进程 ring buffer，并把生命周期/子进程输出追加到同一工作台的 `logs/desktop.log`。

SecretScrubber 在 sink 前屏蔽 API key、Bearer、token 和 URL query 密钥；测试覆盖常见格式。新增 Provider/CLI 时仍不得把完整认证 URL 或环境 dump 传给日志。未知异常 traceback 只进错误日志，API 返回摘要。

日志文件使用轮转 handler（以 `logging_setup.py` 当前参数为准），不是无限增长。维护 API 不清除日志。日志可能包含查询词、项目 id、路径和错误文本，仍属于敏感研究元数据，不应公开上传。

排错顺序：UI Output → `desktop.log`（后端是否被找到/启动/退出）→ `errors.log` → `papercreator.log` → 开发态 `python -m papercreator --check` 或安装态 bundled `papercreator-backend.exe --check`。前端浏览器 console 不等于后端日志。

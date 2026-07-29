> 文档用途：FastAPI 后端专项说明  
> 最后检查：2026-07-28  
> 对应代码：`backend/papercreator/api/`、`core/`  
> 文档状态：可用，发布集成开发中

# 后端系统

- CLI 入口：`papercreator.__main__:main`；支持 serve、`--dev`、`--check`、host/port/log-level；安装态由 `scripts/backend_entry.py` 冻结成 `papercreator-backend.exe`。
- ASGI 工厂：`api/app.py::create_app()`；模块级 `app` 供 Uvicorn。
- 初始化：dotenv → paths → logging → settings → FastAPI → middleware/routes/static；lifespan 中初始化/迁移 DB、同步 Skill、报告 LLM/检索状态。
- 路由：system、settings、projects、search、library、analysis、assistant、writing、agents、skills、prompts、export、versions、workbench；当前源码 157 paths / 181 operations，并由 method/path SHA-256 snapshot 防止静默删改。
- 并发：FastAPI async 网络层 + 4 线程 JobManager。Provider 内部使用 asyncio 并发和按源限流；CPU 算法在线程任务内同步运行。
- 全局状态：缓存 Settings/Paths、EventBus、JobManager、SQLite thread-local connection、HTTP limiter；测试修改路径后必须 reset/reload。
- 工作台：`PAPERCREATOR_WORKBENCH` 解析为唯一 `.papercreator` home；`store/resources.py` 负责分类托管复制、摘要和相对路径，DB schema v6 负责资源、双语目标、提示词、助手对话与归档来源映射。
- 错误：业务异常是 `AppError` 统一 envelope；未知异常写 traceback，响应不返回密钥/堆栈。
- CORS：允许本地 Vite/preview/backend origin 和 `file/null/app`；当前无认证，必须默认回环绑定。
- 关闭：隐藏 `POST /api/system/shutdown` 只接受 Electron 每次启动随机生成的 capability，未配置/错误 token/无 server callback 一律 404；它不进入 OpenAPI。Uvicorn 随后执行 lifespan：取消活动 job、shutdown pool、`wal_checkpoint(TRUNCATE)`、关闭数据库连接。任务取消是协作式。

当前 360 collected；本轮非联网 357 passed/3 deselected。新增快速开始设置 round-trip，DB v6 归档导入/消息脱敏、本地 OCR adapter、sync v2 非重叠章节合并、复杂 DOCX golden、CLI endpoint 与 OpenAPI route snapshot继续通过。当前源码 OpenAPI 157/181；本轮没有重新构建冻结 executable。真实专家金集、10GB、远端认证、费用、真实 OCR 引擎和高并发仍需专项。API 详见 [../api_reference.md](../api_reference.md)。

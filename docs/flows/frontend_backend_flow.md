> 文档用途：Electron、Renderer 和 FastAPI 的交互边界  
> 最后检查：2026-07-27  
> 对应代码：`preload.cjs`、`src/api/`、`state/store.ts`

# 前后端交互流程

Renderer 业务数据全部经 `endpoints` → `client.fetch` → FastAPI；文件/目录选择、show/open path、外链和 backend restart 经 preload IPC。Renderer 不直接使用 Node fs/process。

启动时 `appInfo()` 提供 backend origin；Web 开发没有 preload 时 client 使用默认回环 URL。SSE 使用 `after` seq 重放和 reconnect；store 对 job/done/document/run 事件做增量更新，并用轮询补偿断线。

修改 API 的验收：后端 route/OpenAPI → `api/endpoints.ts` → `api/types.ts` → Store/视图 → 错误/加载/空态 → SSE/轮询一致性 → `npm run build`。

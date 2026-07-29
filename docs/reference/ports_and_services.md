> 文档用途：端口、进程与冲突处理  
> 最后检查：2026-07-27

# 端口与服务

| 服务 | 默认 | 配置 | 暴露 |
|---|---:|---|---|
| FastAPI | 8765 | `PC_PORT` / CLI `--port` | 127.0.0.1 |
| Vite dev | 5173 | `vite.config.ts`/Vite 参数 | localhost dev |
| Vite preview | 4173 | Vite 默认 | localhost dev |
| Ollama | 11434 | `PC_OLLAMA_BASE_URL` | 外部本地服务 |

Electron 若发现 8765 已健康会复用，不会抢占。若被非 PaperCreator 服务占用，health 合同应暴露异常；停止占用进程或统一修改 `PC_PORT`，不要随机杀进程。没有 Docker 端口映射。

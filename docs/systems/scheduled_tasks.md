> 文档用途：记录定时/轮询任务的真实状态  
> 最后检查：2026-07-27  
> 对应代码：`StatusBar.tsx`、`OutputPanel.tsx`、`api/events.ts`  
> 文档状态：当前无服务端定时任务

# 定时任务

后端没有 cron、APScheduler、Celery beat 或随服务启动的周期业务任务。检索、分析、Agent 和维护都由用户/API 显式触发。

| 客户端任务 | 频率 | 位置 | 目的 | 失败影响 |
|---|---:|---|---|---|
| jobs 轮询 | 8 秒 | `components/StatusBar.tsx` | SSE 之外刷新运行状态 | 状态显示延迟，作业本身不受影响 |
| 日志尾部轮询 | 4 秒（面板打开） | `components/OutputPanel.tsx` | 显示后端日志 | 面板陈旧 |
| SSE 重连 | 断开后 timeout | `api/events.ts` | 使用 after seq replay | 超过有界 buffer 时需重新抓状态 |
| Electron health 等待 | 启动时 400ms 循环，最多 90s | `electron/main.cjs` | 等后端就绪 | 超时显示启动失败 |

这些是 UI 定时器，不写业务数据，也不存在重复注册的服务端锁问题。未来增加定时任务必须记录频率、幂等、锁、重试、时区和手动触发接口。

> 文档用途：应用启动与退出流程  
> 最后检查：2026-07-28  
> 对应代码：`electron/main.cjs`、`__main__.py`、`api/app.py`

# 应用启动流程

入口 `electron/main.cjs`：解析显式/开发/已记忆工作台 → 首次无路径时显示原生选择框并 relaunch → 创建/检查 `<folder>/.papercreator` → 在 Chromium 启动前把 userData 指向 `.papercreator/electron` → 获取单实例锁 → 创建 BrowserWindow/菜单/IPC → 如果 8765 已健康则复用，否则开发态找 Python、安装态定位 bundled exe → 注入 `PAPERCREATOR_WORKBENCH` 并 spawn → 每 400ms 检查 health（90s 上限）→ 发 ready/failed → Renderer bootstrap → Zustand `boot()` 拉 health/workbench/resources/projects 并订阅 SSE。

后端：CLI 解析并把实际 bind host/port 传入 app factory（开发热重载子进程同样继承）→ 冻结态跳过源码 repo/.env 探测 → Uvicorn → ensure 单根路径/manifest、日志、设置、中间件、14 个业务 router/static → lifespan `db.init_db()` 升至 schema v6、孤儿 job 处理、只回收分类目录中严格匹配 `.partial-res_[0-9a-f]{16}` 的异常导入 staging、Skill sync、Provider 状态。数据库/路径/迁移失败是硬失败；staging 回收失败只告警，Skill/可选模型/LLM 缺失可降级。

Renderer 从 health 的 `ui.locale` 初始化单一语言，读取项目列表与 `app_state.last_project_id`，存在且有效时直接打开最后项目并进入手稿。Electron 的 AppData 只保存最后工作台定位指针；项目和 locale 权威仍在工作台 `.papercreator/`。第二实例请求在主窗口尚未创建时延迟到窗口可用，避免对 null BrowserWindow 调用原生对话框。

退出：Electron 为 owned backend 注入每次启动随机的 `PC_DESKTOP_SHUTDOWN_TOKEN`；`before-quit` 先关闭 Renderer/SSE，再调用隐藏回环关机路由。Uvicorn 设置 `should_exit`，FastAPI lifespan 请求 job cancel、关闭 pool、执行 WAL truncate checkpoint 并关 DB；Electron 等待 child 退出。6 秒后仍未退出才对精确 owned PID tree 使用 `taskkill /T /F`。正常关闭后随机端口应释放。修改入口/打包路径时同时验证开发态、生产 `resourcesPath/backend`、工作台含空格/中文、后端复用和卸载不删数据。

自动 E2E 使用同一入口但显式设置 `PC_E2E=1`：源码 Electron 不连接 Vite，而加载刚构建的 `dist`，不开 DevTools；`PAPERCREATOR_WORKBENCH` 指向隔离临时目录，`PC_PORT` 使用随机回环端口，`PC_PYTHON` 指向仓库虚拟环境。测试通过 preload 的 `backend.restart()` 真实执行 graceful stop → port release → start，然后页面重新 bootstrap；最终以真实 `app.quit()` 关闭并断言 `Application shutdown complete`、WAL checkpoint、backend exit code 0 和无后端进程。该分支只为可重复测试，不改变正常开发/安装启动逻辑。

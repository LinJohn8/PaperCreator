> 文档用途：源码更新、依赖、构建、重启和数据保护关系  
> 最后检查：2026-07-27  
> 对应代码：`package.json`、`scripts/`、`apps/desktop/package.json`

# 部署更新流程

当前没有可执行的 Git pull 流程，因为根目录无 `.git`。取得新源码后：先停止应用并备份完整工作台 `.papercreator/` → 对比 `.env.example`/pyproject/package lock → `npm install`（Node lock 变化）→ `npm run setup`（Python依赖变化）→ `npm run test:backend` → `npm run build` → 需要安装产物时 `npm run package` → 启动检查 health/workbench/schema/counts。

仅 Python/前端源码开发态改动通常重启/热更新；生产 Renderer 或 bundled backend 改动必须重打包；DB/workbench schema 变化必须先备份并运行对应迁移。重启 ≠ 重建 runtime ≠ 重打安装器 ≠ 删除数据，任何更新都不应清空 `.papercreator`。

当前自包含 NSIS 已在本机完成同路径覆盖安装并确认 Idea/项目/DB 恢复，卸载后工作台也保留。该证据不等于 clean VM/自动升级服务：正式发布仍需签名、版本矩阵、失败回滚和旧 home/workspace 迁移测试。

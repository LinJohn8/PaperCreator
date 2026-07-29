> 文档用途：启动和各视图的数据读取顺序  
> 最后检查：2026-07-28  
> 对应代码：`state/store.ts`、`store/`

# 数据读取流程

`boot()` 先 health，同时读取 workbench、分类资源、项目列表/providers/skills 并建立 SSE；若 `workbench.last_project_id` 仍存在则重新打开。打开项目后并行/按需读取 project bundle、document/stats、library、analyses、agent runs、skills、versions。Landscape detail 可延迟重字段与 term layer，避免一次传全部 grid；Library 默认最多取 200 行，后端支持 limit/offset。

Store 是 UI 缓存，不是权威；工作台目录/计数来自 `GET /api/workbench`，资源记录来自 DB，相对路径每次按当前 home 解析。SSE 事件后根据类型增量更新或 refetch。磁盘文件不会被 watcher 自动注册，用户手动放入 library 后当前需通过 API/恢复流程登记。

Editor 打开项目并每 5 秒读取 `GET /api/writing/{project_id}/sync-status`。后端分别渲染 DB 镜像、扫描托管章节文件，再与 `.papercreator/manuscript-sync.json` v2 的整体及逐节 fingerprint 比较；损坏/v1 基线只会使写入更保守，不阻止读取。若两侧只改了互不重叠的既有章节，UI 可显示修改集合并让用户确认安全合并；同节双改或结构变化仍须显式选边。文件 reindex 后 CodeMirror 通过 external transaction annotation 接收新正文，不触发用户 onChange/dirty。缓存 JSON 损坏不能阻止设置默认启动，但 health/日志应暴露问题。

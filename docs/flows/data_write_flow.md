> 文档用途：业务写入、磁盘同步与破坏性操作顺序  
> 最后检查：2026-07-28  
> 对应代码：`store/`、`vcs/versions.py`

# 数据写入流程

- 工作台初始化：Electron 选择普通目录 → 建 `.papercreator`/可写探测 → 后端 `Paths.ensure` 原子写 `workbench.json` → DB migrations。
- 分类导入：Idea/普通文件走同步端点；代码、数据集、补充材料和待分类目录走 `resource_import` Job。目录先确定性扫描并排除 link/reparse/特殊节点，完成空间预检后写同盘 `.partial-res_*`，4 MiB 分块复制/摘要且响应取消；源身份复核通过后原子 rename，最后才写 `workbench_resources`，再可选 parse/upsert Paper/Collection。取消/失败清 staging 且不产生 ready row；启动只回收严格保留名。外部路径不作为运行依赖。
- 文献：normalize → transaction/upsert → FTS triggers/index → collection/search links。用户字段优先。
- 章节：PATCH SQLite → `flush` 比较 DB/磁盘与双摘要基线 → 安全时写拥有的 manuscript 文件并原子更新基线；磁盘已变时返回 409，不覆盖。flush 只清理自己管理的文件。
- Agent：pre snapshot → 生成 → 落库前检查 flush 安全 → 分步写 SQLite → flush → post snapshot/可选 commit；外部文件已变时不先写 DB。
- Git checkout/restore/Overleaf pull apply：修改权威侧前检查对应方向；安全时 snapshot → 修改 → flush/reindex → 更新基线 → refresh UI。
- 项目 Remote Git pull：fetch tracking refs（不改文件）→ 判断 clean/current branch/ahead/behind → 仅 `ahead=0, behind>0` 继续 → `ensure_sync_safe(reindex)` → DB snapshot + 磁盘手稿备份 → `merge --ff-only` → reindex → refresh；分叉绝不自动 merge。
- 强制选边：读取同步证据 → snapshot（文件覆盖 DB 时）→ 复制确有未同步修改且即将丢失的 DB 或磁盘镜像到项目 `.papercreator/conflicts/` → 覆盖 → 写新基线 → reload document → 只清除与权威侧相同的 dirty。Git discard 还会在工作树变化前保存 binary patch。真实 Electron E2E 已验证普通保存只产生一次受控 409、`diverged`、两个方向的 confirm gate、disk backup、DB snapshot 和回到 `in_sync`；另验证 tracked+untracked 的 discard recovery，以及探索分支 checkout 后磁盘→DB→Editor 一致。
- 设置：区分普通字段和 secret，原子写 JSON（实现细节见 store），reload cached settings。
- Skill：写 SKILL.md → sync registry；builtin 禁止覆盖。
- 最近项目：打开/关闭 Project → `PATCH /api/workbench/state` → SQLite `app_state.last_project_id`。

任何新增覆盖操作必须：验证 resolved path、要求明确 confirm、创建恢复点、报告受影响文件/记录，并在失败时不声称成功。分类目录导入的既有原子合同不可绕过：预扫描和空间检查 → reserved staging → 分块复制/源复核 → 原子 rename → DB 最后登记；不得另建直接写最终路径或先登记 ready 的入口。

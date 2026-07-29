> 文档用途：当前与未来数据/配置迁移需求  
> 最后检查：2026-07-28

# 迁移任务

## 当前事实

SQLite schema 当前为 v6。`core/db.py::init_db()` 的 append-only 迁移链为：v1 基础业务表；v2 `workbench_resources`；v3 `sections.target_words_zh`；v4 `prompt_templates`；v5 `assistant_threads/assistant_messages`；v6 `assistant_thread_imports`。工作台 `workbench.json` 格式 schema 独立为 v1，项目 `manuscript-sync.json` 当前 schema 为 v2。现有数据库由启动过程在 `BEGIN IMMEDIATE` 中自动逐版迁移；不得修改已发布 v1-v6 migration SQL。

### v2 → v3：双语目标

- 旧结构：章节只有 `target_words`。
- 新结构：增加 `target_words_zh INTEGER DEFAULT 0`；读取层在未设置时可按主目标派生 UI 建议，但写回后两者独立。
- 数据风险：0 表示尚未自定义，不等于要求中文 0 字；前端不得把主目标覆盖到用户已设置的中文目标。
- 回滚：启动前备份完整 DB；不支持把 v3 数据降级写回 v2，应恢复备份并使用旧程序。

### v3 → v4：提示词模板

- 新表：`prompt_templates`，工作台作用域使用 NULL `project_id`，项目作用域带 `ON DELETE CASCADE` 外键。
- 兼容：旧工作台迁移后表为空，不影响启动；模板变量由保存层从 `{{name}}` 提取。
- 数据风险：删除项目会删除项目提示词模板；删除前导出/复制仍需后续产品能力。
- 回滚：恢复升级前 DB；不手工降低 `PRAGMA user_version`。

### v5 → v6：助手归档来源映射

- 新表：`assistant_thread_imports`，保存显式 `scope_key`、来源 thread id、完整内容 fingerprint、新本地 thread 外键、来源 scope 与导入时间；项目删除和线程删除按外键级联。
- 兼容：旧工作台迁移后表为空，已有线程/消息不改写。归档恢复总是创建新本地 ID，不覆盖现有线程。
- 幂等：目标作用域内相同来源 ID+fingerprint 且本地线程仍存在时跳过；本地副本删除后允许恢复；来源内容变化产生新副本。
- 回滚：恢复升级前完整 DB；不得只删映射表或降低 `PRAGMA user_version`，否则会失去导入幂等审计。

## 旧 home/workspace 到单根工作台

- 旧结构：安装数据可能位于平台 AppData home，项目可通过 `PAPERCREATOR_WORKSPACE` 分散在另一个目录。
- 新结构：用户选择普通文件夹，全部数据放在其 `.papercreator/`；项目在 `projects/`，分类输入在 `library/`。
- 当前兼容：headless/test 仍支持 `PAPERCREATOR_HOME/WORKSPACE`，但桌面新流程使用 `PAPERCREATOR_WORKBENCH`。
- 未完成：没有自动探测/复制旧安装数据的迁移向导。若存在真实旧用户数据，必须先停止服务、完整备份 home+workspace，再制定一次性复制/路径修复/计数校验；不能仅移动项目目录。
- 回滚：保留原目录不修改；新工作台验证 DB schema、项目、library、Skill 和手稿后再决定归档。

## 已识别的未来迁移

### 配置优先级/来源

- 旧结构：env layer 后被 settings/secrets JSON 覆盖，文件无显式 source/version。
- 目标：待决策；建议 env 强制层 + source metadata。
- 兼容：保留旧 JSON，首次启动只解释来源，不自动删除字段。
- 回滚：备份 config 目录；允许恢复旧 merge 规则。
- 验证：同字段 defaults/env/settings/secrets 组合测试。

### 手稿同步基线（schema v2 已完成）

- 旧结构：项目文件没有共同基线；DB 与 `manuscript/*.md` 同时存在时只能人工判断。
- 当前结构：项目 `.papercreator/manuscript-sync.json`，format `papercreator-manuscript-sync`、schema v2；每个 document 保存整体 DB/disk 摘要、数量、同步时间，以及每个 section key 的 filename/DB/disk fingerprint。
- 兼容：schema v1 继续读取为整体可信基线，但没有逐节 fingerprint，不能自动合并；无基线且两侧摘要相等可在下一次安全同步建立 v2；仅一侧存在时只允许该侧成为来源；两侧不同一律 `untracked_divergence`。损坏 JSON 视为无可信基线并在日志报告。
- 数据迁移：没有批量改写旧项目，也不需要 DB schema 迁移。用户第一次显式选边时，系统先保存被覆盖侧；文件覆盖 DB 另建 snapshot。
- 回滚：删除基线不会删除正文，但会回到更保守的旧项目判断；不得为消除告警手工伪造摘要。
- 已验证：相等基线、DB-only/disk-only/双边分叉、强制恢复副本、Git checkout/discard、snapshot restore、Agent/Overleaf 前置保护；不同章节双边修改可在 token 未陈旧时合并，并先建 snapshot/两侧镜像；同节双改、增删/重命名和 v1 基线继续阻塞。
- 后续建议：如实现同节内容级三方 merge，应新增 schema v3 保存共同祖先内容或可验证 patch；不得原地改变 v1/v2 语义，也不得以时间戳猜测。

### 自包含发行路径（已完成代码迁移）

- 旧结构：安装包 backend 源码 + 外部 Python。
- 新结构：PyInstaller onedir 作为 electron-builder extraResources；安装态不回退系统 Python。
- 数据兼容：应用二进制与 `<workbench>/.papercreator` 分离；覆盖安装/卸载已验证不删除工作台。
- 回滚：保留旧安装程序和完整工作台备份；禁止卸载器删除用户数据。

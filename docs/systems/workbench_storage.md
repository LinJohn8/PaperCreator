> 文档用途：定义用户所选工作台、`.papercreator` 单根存储和分类合同  
> 最后检查：2026-07-28  
> 对应代码：`core/paths.py`、`store/resources.py`、`api/routes/workbench.py`、`electron/main.cjs`  
> 文档状态：可用；DB schema v6 / workbench manifest schema v1

# 工作台存储系统

## 核心合同

PaperCreator 是安装软件，不把论文数据放进安装目录。用户选择一个普通文件夹 `W` 后，全部托管内容只能位于 `W/.papercreator/`。停止应用后复制这一目录，应得到可移动、可恢复的完整工作台。

`PAPERCREATOR_WORKBENCH=W` 是桌面产品入口；`PAPERCREATOR_HOME` 与 `PAPERCREATOR_WORKSPACE` 仅供旧 headless/test 兼容。AppData 的 `workbench-location.json` 只是定位指针，不是业务数据根。

## 分类

| 类别 | 目录 | 含义 | 是否生成 Paper |
|---|---|---|---|
| 新论文 | `projects/<slug>` | 正在撰写的论文项目、章节、集合、分析、导出、Git | 项目本身不是 Paper |
| Idea | `library/ideas` | 研究问题、方法设想、贡献假设 | 是，origin=idea |
| 参考论文 | `library/reference-papers` | 他人论文/书目/PDF | 可解析或建最小 Paper |
| 我的论文 | `library/own-papers` | 已发表成果、旧稿、现有手稿 | 可解析或建 origin=own_paper |
| 项目代码 | `library/code-projects` | 实验实现或 Git 仓库托管副本 | 否 |
| 数据集 | `library/datasets` | 实验输入/输出数据 | 否 |
| 补充材料 | `library/supplementary` | 图片、表格、协议等 | 否 |
| 待分类 | `library/inbox` | 暂时无法分类的材料 | 视文件而定；当前不自动解析 |

写作项目与输入资料分开是稳定产品语义；不要把“自己的旧论文”当成新写作项目，也不要把代码塞进项目手稿目录作为全局共享输入。

## 导入与可移动性

Idea 和普通文件由同步端点先复制/生成托管内容，再解析/注册。项目代码、数据集、补充材料和待分类目录由 `resource_import` Job 执行：确定性预扫描与空间检查 → link/reparse/特殊节点排除 → 同分类 `.partial-res_*` 4 MiB 分块复制/摘要 → 文件与目录源身份复核 → 同盘原子 rename → DB 最后登记。取消/失败不产生 ready row并清 staging；异常退出后启动只回收严格保留名。

DB `workbench_resources.managed_path` 保存相对 `.papercreator` 的 POSIX 路径；绝对 `original_path` 只用于 provenance，源文件移动/删除不影响已经成功导入的运行。文件和目录保存 SHA-256、字节数；目录还保存文件数和 `metadata.import` audit（strategy、source/copied 计数、excluded、link policy、space preflight）。

代码目录会排除 `node_modules`、虚拟环境、Python/JS 缓存、`dist/build/target` 等生成目录，以及 `.env` 和非 example 的 `.env.*`。排除项写入 metadata，导入并非静默“完整镜像”。

## 删除、备份和迁移

- `DELETE /api/workbench/resources/{id}` 默认只忘记 DB 注册，保留托管文件。
- `remove_files=true` 只允许删除 `library/` 内资源，不能触及 home、projects 或外部来源。
- 切换工作台不会复制或删除旧目录；迁移应在应用停止后整体复制 `.papercreator`，再选择新父目录。
- DB schema v6 和 `workbench.json` schema v1 是不同版本域；升级其中一个不能机械修改另一个。DB v2 引入资源、v3 引入双语目标、v4 引入提示词、v5 引入助手对话、v6 引入归档导入来源映射。
- 大目录后台化、失败原子性、进度、取消和空间预检已经完成。当前开放项是实际 10GB/百万小文件、网络盘、长路径、低空间卷、普通权限 junction/reparse 与异常中断基准；当前没有断点续传，异常重启会清 staging 后重来。

## 修改影响

修改分类名/目录要同步 Paths、resource store、DB/API/TS 类型、WorkbenchPanel、迁移、备份/卸载测试和本文。修改 Electron userData 设置必须发生在首个 BrowserWindow/Chromium profile 创建前，否则会把状态散落回 AppData。

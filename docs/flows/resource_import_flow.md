> 文档用途：Idea、论文、代码、数据和补充材料的托管导入流程  
> 最后检查：2026-07-28  
> 对应代码：`WorkbenchPanel.tsx`、`api/routes/workbench.py`、`store/resources.py`、`api/app.py`

# 分类资源导入流程

## 两条实际入口

- Idea、普通文件和论文文件仍使用同步 `POST /api/workbench/resources`：先创建托管副本，再注册资源；论文类随后解析或建立最小 Paper。
- `code_project`、`dataset`、`supplementary`、`inbox` 的目录使用 `POST /api/workbench/resources/import`：立即返回 HTTP 202 和 `resource_import` Job，由后台执行可取消的原子托管复制。文件不能误传给此端点，目录也不能误传给同步端点。

~~~mermaid
flowchart TD
    UI[WorkbenchPanel 选择分类] --> TYPE{Idea / 文件 / 目录?}
    TYPE -->|Idea| IDEA[生成 library/ideas Markdown]
    TYPE -->|普通文件或论文| FILE[同步资源端点]
    TYPE -->|四类目录| PICK[Electron 原生目录选择]
    PICK --> POST[POST /api/workbench/resources/import]
    POST --> JOB[202 + resource_import Job]
    JOB --> SCAN[确定性预扫描文件与空目录]
    SCAN --> SAFE{根/节点安全?}
    SAFE -->|根是 symlink/junction/reparse| FAIL[Job failed；无 ready 记录]
    SAFE -->|嵌套 link/reparse/特殊文件| EXCLUDE[不跟随并记录 excluded audit]
    SAFE -->|普通节点| SPACE[目标卷空间预检 + 安全余量]
    EXCLUDE --> SPACE
    SPACE --> STAGE[同分类 .partial-res_<16hex> staging]
    STAGE --> COPY[4 MiB 分块复制并计算 SHA-256]
    COPY --> CHECK[每文件前后校验 size/mtime/device/inode/resolve；目录再校验]
    CHECK --> COMMIT[同盘原子 rename 到最终托管路径]
    COMMIT --> REG[最后写 workbench_resources ready row]
    REG --> POSTPROC[可选 Paper/Collection 处理]
    POSTPROC --> DONE[Job done；UI/Library/项目刷新]
    JOB -. 取消或失败 .-> CLEAN[清理 staging；不产生 ready row]
    START[应用启动] --> STALE[只回收严格匹配的 stale .partial-res_<16hex>]
~~~

## 安全与提交合同

预扫描生成稳定 inventory、总文件数和字节数。代码项目排除依赖/构建目录及 `.env*`，但保留 `.env.example`；数据集等分类不会套用代码依赖排除。根 symlink、junction 或其他 Windows reparse point 被拒绝；嵌套 link/reparse 与特殊文件不复制，也不跟随到源根之外，并写入 `metadata.import` 审计。

复制前后会校验源文件的 size、mtime、device、inode，并确认 resolved path 仍在源根内；空目录和目录节点也在提交前复核。源在扫描与复制间变化时以 `resource_import_source_changed` 失败，而不是提交不一致副本。空间不足返回 `resource_import_insufficient_space`。

目标先写同一分类目录中的保留 staging，分块期间响应 cooperative cancellation 并持续更新 Job/SSE。全部复制和校验成功后才同盘 rename；数据库 `workbench_resources` 登记是最后提交点。登记失败会撤销最终副本；取消/失败会清 staging。若清理本身失败，Job 返回 `resource_import_cleanup_failed` 和 reserved path，不能把残留当成 ready 资源。

异常进程终止可能留下 staging。应用启动只自动回收名称严格匹配 `^.partial-res_[0-9a-f]{16}$` 的分类目录直属项，不触碰 `.partial-user-data` 等相似用户目录；回收失败仅告警，不阻塞主服务启动。

## 输入、输出与删除

输入为 `kind`、目录 `source_path`，以及可选 title、description、project_id 和 metadata；HTTP 响应给出 `job_id`，最终 Job result 包含资源、可选 Papers/warnings。`metadata.import` 至少记录 `strategy=atomic_managed_copy`、source/copied files/bytes、excluded count、link policy 和 space preflight。

同步论文文件的既有边界不变：Bib/RIS/CSV/JSON 从托管副本解析并 upsert；PDF/DOCX/MD/TEX 建立最小 Paper；不支持格式可安全复制但返回未解析 warning。删除默认只删注册；显式删文件仍必须通过 `library/` containment。

修改此流程必须同时回归：取消/失败无 ready row、staging 清理、源变化、空间不足、link/reparse 分类、代码密钥与依赖排除、空目录保留、相对路径随完整工作台移动、DB 登记失败撤销副本，以及 Renderer 丢失 SSE 后仍可从 durable Job 完成等待。

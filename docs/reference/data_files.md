> 文档用途：关键数据文件、生成者、恢复和备份  
> 最后检查：2026-07-28  
> 对应代码：`core/paths.py`、`store/documents.py`、`store/resources.py`

# 数据文件

| 路径 | 内容 | 可重建 | 备份/删除规则 |
|---|---|---|---|
| `<workbench>/.papercreator/workbench.json` | 产品/格式/schema v1/创建时间 | 可谨慎重建 | 工作台标记；不要与 DB schema 混淆 |
| `<home>/papercreator.db[/-wal/-shm]` | 全局业务状态 | 否 | 停服务后一致备份；不可只随意删 WAL |
| `<home>/config/settings.json` | UI 非密配置 | 可手配 | 建议备份 |
| `<home>/config/secrets.json` | API keys/tokens | 否 | 安全备份；禁止提交 |
| `<home>/logs/*.log*` | 轮转日志 | 否 | 可在停服务后清理，含研究元数据 |
| `<home>/cache/http` | API cache | 是 | 可由 maintenance 清理 |
| `<home>/cache/extracted/<resource_id>.txt` | 自有/参考论文完整提取文本 sidecar | 是，可从托管原文件重提取 | 删除资源时只删精确对应 sidecar；扫描 PDF 在 OCR 前可能为空 |
| `<home>/cache/embeddings` / DB embeddings | 向量 cache | 是 | 清理会导致重算/费用 |
| `<home>/models` | 模型权重 | 是 | 大文件，重下慢 |
| `<home>/library/ideas` | Idea Markdown 托管副本 | 否 | 必须备份 |
| `<home>/library/reference-papers` | 他人论文/书目/OA PDF | 不保证 | 必须按用户资料管理 |
| `<home>/library/own-papers` | 自己论文和旧稿 | 否 | 必须备份 |
| `<home>/library/code-projects` | 排除依赖/密钥后的代码副本 | 原来源可能不存在 | 必须备份；不等同完整源仓镜像 |
| `<home>/library/datasets|supplementary|inbox` | 数据、补充材料、待分类 | 否 | 按核心输入备份 |
| `<home>/library/*/.partial-res_<16hex>` | 尚未提交的目录导入 staging | 是 | 不备份；活跃 Job 不得删除；取消/失败清理，异常终止后启动只回收严格名称，不碰相似用户目录 |
| `<home>/skills` | 用户 Skill | 否 | 必须备份 |
| `<home>/projects/<slug>/manuscript` | 章节/合并稿 | 否 | 用户核心资产 |
| `<project>/.papercreator/manuscript-sync.json` | format/schema v2、每文档整体摘要、逐节 filename/DB/disk fingerprint 与同步时间；兼容读取 v1 | 可在两侧相等时重建 | 分叉时不得删除或伪造；v1 无逐节可信基线，不能自动合并；随项目备份 |
| `<project>/.papercreator/conflicts/*` | 强制同步被覆盖侧、`conflict.json`、Git discard binary patch | 否 | 人工确认恢复完成前不得清理 |
| `<project>/.papercreator/imports/*` | 两阶段手稿导入的源文件审计副本 | 原来源可能不存在 | 随项目备份；不是 cache，不由普通 maintenance 清理 |
| `<project>/.papercreator/venue-templates.json` | 投稿模板包来源、许可证、SHA-256、导入时间和文件数 | 可从 assets 部分重建但会丢 provenance | 随项目备份；不要与 workbench manifest 混淆 |
| `<project>/assets/venue-templates/<slug>` | 用户授权导入的 publisher/会议/Overleaf 排版包 | 原下载可能失效 | 随项目备份；第三方许可证约束；导入阶段不执行其中代码 |
| `<project>/.papercreator/skills` | 项目 Skill | 否 | 随完整项目备份；默认 `.gitignore` 不纳入项目 Git |
| `<project>/.git` | 项目历史 | 否 | 禁止删除 |
| `<project>/exports` | 派生输出 | 多数可 | 以源稿为准；人工修改的输出例外 |
| `<home>/electron` | Chromium userData/cache/local storage | 多数可 | 删除会丢 UI/session 状态，不丢 DB 手稿 |
| `<home>/logs/desktop.log` | Electron 与 bundled 后端生命周期 | 否 | 可停应用后清理；含路径/错误元数据 |

开发默认 home 是仓库 `.papercreator/`；安装默认 home 是用户所选普通文件夹内的 `.papercreator/`。AppData 只保存定位指针。推荐备份整个 home，不要依据本表逐项拼凑不完整备份。

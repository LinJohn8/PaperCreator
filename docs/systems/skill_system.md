> 文档用途：SKILL.md 的发现、覆盖、启用和 Agent 注入  
> 最后检查：2026-07-27  
> 对应代码：`backend/papercreator/skills/`、`resources/skills/`  
> 文档状态：可用

# Skill 系统

Skill 是声明式 prompt 资产，不是任意代码插件。目录优先级为 builtin < user < project，同 id 后者覆盖前者：

1. `backend/papercreator/resources/skills/<id>/SKILL.md`：随应用、只读；当前 4 个。
2. `<home>/skills/<id>/SKILL.md`：手工/UI/LLM 创建。
3. `<project>/.papercreator/skills/<id>/SKILL.md`：随项目 Git 协作。

文件记录 name/description/version/roles/triggers/tags/priority/instructions；SQLite `skills` 只缓存元数据、启用状态和 usage。启动和写入后执行 sync，单个坏文件跳过。Runner 按 role、显式 id、trigger 和 token budget 选择/拼接，报告未加载原因。

UI 支持 CRUD、copy/import、enable、preview/suggest 和 LLM draft。内置内容要编辑时先复制到 user。Skill 指令可能造成 prompt injection 或错误规范，因此来源、预览和 token budget 仍需用户审查；当前无数字签名/权限沙箱。

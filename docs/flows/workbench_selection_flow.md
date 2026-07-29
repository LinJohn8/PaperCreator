> 文档用途：首次选择、记忆和切换工作台的真实流程  
> 最后检查：2026-07-27  
> 对应代码：`apps/desktop/electron/main.cjs`、`core/paths.py`

# 工作台选择流程

~~~mermaid
sequenceDiagram
    participant U as 用户
    participant E as Electron main
    participant A as AppData 指针
    participant W as 所选文件夹
    participant B as Bundled backend
    E->>E: 读取 PAPERCREATOR_WORKBENCH / 开发默认 / 已记忆路径
    alt 没有工作台
        E->>U: Welcome + 原生目录选择
        U->>E: 选择现有可写文件夹
        E->>A: 写 workbench-location.json
        E->>E: relaunch
    end
    E->>W: 创建/检查 .papercreator
    E->>E: userData = W/.papercreator/electron
    E->>B: env PAPERCREATOR_WORKBENCH=W
    B->>W: ensure 目录 + manifest + DB migration
~~~

路径是目录时才接受；若用户误选 `.papercreator` 本身，launcher 规范化为其父目录。后端启动前执行可写探测。首次选择必须 relaunch，因为 Chromium profile 路径必须早于 Renderer 创建。

切换入口显示新旧位置和“旧工作台不会移动/删除”，确认后更新指针并 relaunch。当前没有自动复制/合并工作台；这避免隐式覆盖，但需要未来迁移向导。

异常：指针 JSON 无效或路径不存在时回到选择；工作台不可写时显示错误；bundled backend 缺失时不回退系统 Python。修改时必须验证路径含空格/中文、只读盘、网络盘、第二实例和异常退出。

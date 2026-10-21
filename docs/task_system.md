# 项目任务系统

参考 [s10 完整 code.py](https://github.com/shareAI-lab/learn-claude-code/blob/main/s10_task_system/code.py)，将原来的 `todo_write` 替换为六个任务工具。基础工具、权限钩子、子智能体、技能、压缩、记忆与流式输出继续共用原来的主循环。

## 结构和职责

- `task_system/store.py`：Task 数据结构、JSON 文件、依赖检查、领取与完成。
- `tool/task_system.py`：模型可以调用的处理函数；读取当前 `workspace.WORKDIR`，不依赖模型客户端。
- `tool/registry.py`：六个工具的中文说明、参数结构和处理函数映射。
- `agent/prompts.py`：引导模型安排、执行和更新任务。
- `frontend/src/components/ProjectTasks.jsx`：展示项目任务、状态、依赖和领取者。

任务保存在所选工作项目的 `.tasks/task_XXXXXXXX.json`，目录在第一次创建任务时生成，读取空项目不会创建目录。同目录的桌面会话和命令行共用记录，不同项目互相隔离。`.tasks/` 已加入 Agent 项目的 `.gitignore`。

每条记录包含 `id`、`subject`、`description`、`status`、`owner`、`blockedBy`。ID 由程序随机生成；`blockedBy` 保存前置任务的 ID。依赖完成后仍保留该 ID，是否阻塞取决于前置任务的当前状态。

## 工具和执行流程

| 工具 | 行为 |
| --- | --- |
| `create_task(subject, description="")` | 创建 pending、尚未领取的任务，返回包含真实 ID 的 JSON |
| `update_task(task_id, addBlockedBy)` | 添加依赖；只允许 pending 且未领取的任务，拒绝缺失、自身和循环依赖 |
| `list_tasks()` | 查看项目所有任务及尚未完成的依赖 |
| `get_task(task_id)` | 返回完整任务 JSON |
| `claim_task(task_id)` | 检查 pending 和全部依赖完成后，设为 in_progress 并记录领取者 |
| `complete_task(task_id)` | 检查进行中和领取者，设为 completed，报告本次新解锁的任务 |

例如 A 是实现设备查询接口，B 是验证接口：

1. 模型调用 `create_task` 创建 A、B，工具返回实际 ID。
2. 下一次模型请求中，模型使用实际 ID，通过 `update_task` 设置 B 的 `addBlockedBy=[A 的 ID]`。
3. 模型领取 A，使用文件工具、命令或 `task` 委派子智能体完成实际工作。
4. 主智能体验证后调用 `complete_task(A)`，B 解除阻塞。
5. 模型领取 B，执行验证，再调用 `complete_task(B)`。

任务状态机为 `pending → claim_task → in_progress → complete_task → completed`。等待依赖是 pending 的一种展示状态，不是第四种存储状态。Python 检查依赖和状态，模型决定任务拆分、工具调用和下一步执行。没有额外的自动规划请求，也不自动分发子智能体；完成状态本身不证明代码已通过测试。

六个工具只提供给主智能体。s06 的 `task(prompt)` 仍负责运行子智能体，与创建任务记录的 `create_task` 是两件事。主智能体领取任务后可以自己执行，也可以委派；子智能体返回结论后由主智能体更新记录。

## 桌面、恢复和并发

桌面输入区上方显示可展开的“项目任务”，展示完成数量、标题、描述、任务 ID、领取者和未完成依赖。后端的 `get_session` 与 `poll` 从 `.tasks` 返回最新任务快照，其他会话的更新也能显示。任务文件损坏时保留原文件，显示中文读取错误，聊天仍可载入。

桌面以会话 ID 作为稳定的 owner，同一会话重启后仍可继续自己领取的任务，其他会话可以查看但不能标记完成。命令行沿用示例的默认 owner `agent`。模型不接受 owner 参数，工具处理函数设置当前身份。owner 是应用内的协作标记，不是操作系统级访问控制。

与单进程参考示例相比，这里增加进程间文件锁，将依赖检查、领取和状态更新放在同一临界区，防止两个桌面进程同时领取一项任务。单条记录采用临时文件加原子替换，写入失败保留之前的状态；这不构成跨多个任务文件的数据库事务。

停止不会把已执行的操作撤销，也不会自动把进行中的任务改为已完成；恢复后可在原会话继续。当前不提供删除、取消、重新分配或自动重试工具。依赖缺失或损坏时按未完成处理，不放行下游任务。

新会话文件不再保存 todo 状态。旧会话内的 `todos` 字段及 `todo_write` 界面记录继续保留为历史，不恢复旧工具，不自动导入共享任务，避免把多个旧会话的计划混在一起。任务文件与消息分别保存，压缩上下文不会清除任务状态；继续工作时模型通过 `list_tasks` 和 `get_task` 读取记录。

## 验证

unittest 覆盖任务持久化、实际 ID、依赖解除、多前置条件、循环拒绝、错误输入、原子写入失败、损坏文件保留、跨进程重复领取、SDK 工具结果配对、权限拒绝、压缩后状态保留、桌面同项目共享、跨项目隔离和重启后的领取者身份。前端状态测试覆盖四种展示状态和无新增聊天事件时的任务更新；`tests/desktop_tasks_ui.cjs` 使用临时项目和模拟工作进程验收依赖、进度、共享、恢复、主题、键盘和窄窗口。

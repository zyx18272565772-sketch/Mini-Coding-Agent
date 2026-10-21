"""提供给模型的工具定义，以及工具名到执行函数的映射。"""

from tool.bash import run_bash
from tool.compact import run_compact
from tool.edit_file import run_edit
from tool.glob import run_glob
from tool.load_skill import run_load_skill
from tool.read_file import run_read
from tool.task import run_task
from tool.task_system import (
    run_create_task, run_update_task, run_list_tasks,
    run_get_task, run_claim_task, run_complete_task,
)
from tool.write_file import run_write


BASE_TOOLS = [   #给模型看的，主智能体和子智能体共用的工具
    {
        "name": "bash",
        "description": "执行一条系统命令。仅对可独立执行的有限时长命令设置 run_in_background=true；立即返回任务编号，完成后系统自动交付结果。",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "minLength": 1},
                "run_in_background": {"type": "boolean", "description": "默认 false。后台命令完成后会自动通知当前智能体。"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_file",
        "description": "读取文件内容，可通过 limit 参数只读取前 N 行。",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "将内容写入文件，必要时自动创建父目录。文件已存在时会覆盖原有内容。",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "精确匹配文件中的文本，仅替换第一次出现的位置。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "glob",
        "description": "按通配符模式查找路径，** 表示递归匹配子目录。",
        "input_schema": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "load_skill",
        "description": "按技能名称读取完整的 SKILL.md 说明，获取完成对应任务的步骤和要求。",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "可用技能目录中的名称。"}},
            "required": ["name"],
        },
    },
    {
        "name": "compact",
        "description": "在任务阶段结束后请求压缩会话历史，保留进度以便继续工作；本批工具全部执行后生效。",
        "input_schema": {"type": "object", "properties": {}},
    },
]

BASE_HANDLERS = {
    "bash": run_bash,
    "read_file": run_read,
    "write_file": run_write,
    "edit_file": run_edit,
    "glob": run_glob,
    "load_skill": run_load_skill,
    "compact": run_compact,
}

TASK_ID_SCHEMA = {"type": "string", "pattern": "^task_[0-9a-f]{8}$"}
TASK_SYSTEM_TOOLS = [
    {
        "name": "create_task",
        "description": "创建项目任务，返回运行时生成的真实任务编号；先创建全部任务，再用编号建立依赖。",
        "input_schema": {
            "type": "object",
            "properties": {"subject": {"type": "string", "minLength": 1}, "description": {"type": "string"}},
            "required": ["subject"], "additionalProperties": False,
        },
    },
    {
        "name": "update_task",
        "description": "给尚未领取的任务添加前置依赖；使用 create_task 返回的真实编号，禁止自身依赖和循环依赖。",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": TASK_ID_SCHEMA, "addBlockedBy": {"type": "array", "items": TASK_ID_SCHEMA, "minItems": 1}},
            "required": ["task_id", "addBlockedBy"], "additionalProperties": False,
        },
    },
    {
        "name": "list_tasks",
        "description": "查看当前项目的任务、状态、领取者和尚未完成的依赖；继续已有工作时先检查，避免重复创建。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_task",
        "description": "按任务编号读取完整标题、描述、状态、领取者和依赖。",
        "input_schema": {"type": "object", "properties": {"task_id": TASK_ID_SCHEMA}, "required": ["task_id"], "additionalProperties": False},
    },
    {
        "name": "claim_task",
        "description": "领取一个依赖全部完成的待处理任务，标记为进行中；尚有依赖或已经被领取时会拒绝。",
        "input_schema": {"type": "object", "properties": {"task_id": TASK_ID_SCHEMA}, "required": ["task_id"], "additionalProperties": False},
    },
    {
        "name": "complete_task",
        "description": "实际执行并验证后，将当前会话领取的任务标记完成，返回因此解除阻塞的任务；不会自动执行下一项。",
        "input_schema": {"type": "object", "properties": {"task_id": TASK_ID_SCHEMA}, "required": ["task_id"], "additionalProperties": False},
    },
]

TASK_TOOL = {
    "name": "task",
    "description": "使用独立聊天记录执行一个明确的子任务，等待子智能体完成并返回最终文本。",
    "input_schema": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "minLength": 1,
                "description": "子任务的目标、必要背景和期望结果；子智能体不继承主对话历史。",
            },
        },
        "required": ["prompt"],
    },
}

TOOLS = [*BASE_TOOLS, *TASK_SYSTEM_TOOLS, TASK_TOOL]
TOOL_HANDLERS = {          #给程序用的
    **BASE_HANDLERS,
    "create_task": run_create_task,
    "update_task": run_update_task,
    "list_tasks": run_list_tasks,
    "get_task": run_get_task,
    "claim_task": run_claim_task,
    "complete_task": run_complete_task,
    "task": run_task,
}

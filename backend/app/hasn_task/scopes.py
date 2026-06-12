"""任务 / 工作流（hasn_task）scope 展示元数据。

设计事实源：模块 12（任务系统 + 多任务编排 DAG）；16-工具授权统一 D-v3-3（app 域 scope
元数据随应用目录落地，由 `app/mcp/scopes.py` 聚合）。判定真相是工具 required_scopes + 三态 mode。

task / workflow 同属 hasn_task 应用域（workflow 节点复用 task），故 domain 统一为 'task'。
task:create 已废弃（R5 收口）：建/改并入 task:manage、触发并入 task:run。
"""

from __future__ import annotations

HASN_TASK_SCOPE_CATALOG: dict[str, dict[str, str]] = {
    'task:read': {'label_zh': '查看任务进度与结果', 'domain': 'task', 'risk': 'low', 'description': '查任务定义/run/结果/历史（hasn.task.list/get/list_runs/get_run/query_results）'},
    'task:manage': {'label_zh': '管理任务', 'domain': 'task', 'risk': 'medium', 'description': '建/改/暂停/恢复/删任务（hasn.task.create/update/pause/resume/delete）'},
    'task:run': {'label_zh': '触发任务执行', 'domain': 'task', 'risk': 'medium', 'description': '立即触发一次任务执行（hasn.task.run_now）'},
    'workflow:read': {'label_zh': '查看工作流', 'domain': 'task', 'risk': 'low', 'description': '查工作流图/节点结果/执行历史、发现可用分身（hasn.workflow.get/get_node_result/list/list_agents）'},
    'workflow:manage': {'label_zh': '管理工作流', 'domain': 'task', 'risk': 'medium', 'description': '建/增删节点与边/暂停/取消工作流（hasn.workflow.create/add_node/add_edge/pause/cancel）'},
    'workflow:run': {'label_zh': '触发工作流执行', 'domain': 'task', 'risk': 'medium', 'description': '立即触发一次整图执行（hasn.workflow.run）'},
}

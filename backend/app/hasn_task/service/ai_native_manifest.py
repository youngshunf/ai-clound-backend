"""任务系统（hasn_task，模块 12）AI-Native 内置 manifest + App 声明。

设计事实源：
- docs/hasn-node设计文档/12-任务系统实施方案/06-任务系统AI-Native应用化重构设计.md §4/§5
- docs/hasn-node设计文档/14-AI-Native应用平台/16-应用目录与商业化管理统一设计.md（catalog/manifest 四正交概念）
- docs/hasn-node设计文档/14-AI-Native应用平台/15-AI-Native应用命名空间与目录约定.md（ADR-15）

⚠️ 方案 A（同 deck manifest）：`tools[]` **置空数组**——`hasn.task.*` 工具数据面在本地
（hasn-mcp `built_in_task_tools()`，source=Local），不经云端 Runtime Gateway；`capabilities[]`
只承载发现/权限元数据控制面记录。云端 Agent API（/api/v1/hasn-task/agent）是补线（云端
Runtime Host / 跨设备读权威），经 Agent JWT + scope 闸直达，本身不走 manifest 路由。

⚠️ 工具调用授权（D4，福仔纠正）：manifest 只声明各工具 `risk_level` 作为调用授权默认值；
最终由统一授权开关（owner 对 agent 的 capability_modes 三态）在工具网关**调用时**强制。
任务工具不自管授权；「agent 建周期任务待主人确认」是任务业务态 pending_approval（不走 ask_gate）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.hasn.service.app_catalog_registry import App

_AUDIT_FIELDS = [
    'trace_id',
    'workspace',
    'app_id',
    'agent_hasn_id',
    'owner_hasn_id',
    'session_uuid',
    'tool_id',
    'required_scopes',
    'decision',
]

_SCOPE_READ = 'task:read'
_SCOPE_MANAGE = 'task:manage'
_SCOPE_RUN = 'task:run'

_WSCOPE_READ = 'workflow:read'
_WSCOPE_MANAGE = 'workflow:manage'
_WSCOPE_RUN = 'workflow:run'


def _cap(
    *,
    name: str,
    title: str,
    description: str,
    scope: str | None,
    risk_level: str,
    properties: dict,
    required: list[str],
    page_rank: int,
    tags: list[str],
) -> dict:
    """hasn.task.* 能力声明（读/写类一律出厂 Allow 免确认，16-doc D-v3-1；owner 可设 ask/deny override）。"""
    return {
        'capability_id': f'hasn_task.{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'hasn_task.{name}',
        'mcp_name': f'hasn.task.{name}',
        'required_scopes': [scope] if scope else [],
        'workspace_roles': ['owner'],
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': required,
            'additionalProperties': False,
        },
        'output_schema': {'type': 'object'},
        'risk_level': risk_level,
        'human_confirmation': {'required': False},
        'result_writeback': ['audit', 'agent_message'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': tags,
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


def _wcap(
    *,
    name: str,
    title: str,
    description: str,
    scope: str | None,
    risk_level: str,
    properties: dict,
    required: list[str],
    page_rank: int,
    tags: list[str],
) -> dict:
    """hasn.workflow.* 能力声明（多任务编排 DAG，节点复用 task；读/写类一律出厂 Allow，16-doc D-v3-1）。"""
    return {
        'capability_id': f'hasn_task.workflow_{name}.capability',
        'name': title,
        'description': description,
        'tool_id': f'hasn_task.workflow_{name}',
        'mcp_name': f'hasn.workflow.{name}',
        'required_scopes': [scope] if scope else [],
        'workspace_roles': ['owner'],
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': required,
            'additionalProperties': False,
        },
        'output_schema': {'type': 'object'},
        'risk_level': risk_level,
        'human_confirmation': {'required': False},
        'result_writeback': ['audit', 'agent_message'],
        'discovery': {
            'exposure': 'on_demand',
            'summary': description,
            'tags': tags,
            'schema_visibility': 'authorized_agents',
            'default_page_rank': page_rank,
        },
    }


_NODE_SPEC = {
    'type': 'array',
    'description': (
        '节点列表 [{node_key, agent_id?, prompt, system_prompt?, '
        'skill_bundle_refs?, enabled_toolsets?, enable_subagents?}]'
    ),
}
_EDGE_SPEC = {'type': 'array', 'description': '依赖边列表 [{parent, child}]（建图时无环校验）'}

_WORKFLOW_CAPABILITIES = [
    _wcap(
        name='create',
        title='建工作流',
        description=(
            '一次声明整图（节点+依赖边），把大任务拆成多个可并行/交叉/依赖的子任务。节点可跨分身。'
            'agent 建带定时的工作流 → pending_approval 业务态待主人确认；一次性直接可跑。'
        ),
        scope=_WSCOPE_MANAGE,
        risk_level='medium',
        properties={
            'name': {'type': 'string', 'minLength': 1, 'description': '工作流名称'},
            'goal': {'type': ['string', 'null'], 'description': '总目标（整图验收口径）'},
            'nodes': _NODE_SPEC,
            'edges': _EDGE_SPEC,
            'schedule_type': {'enum': ['once', 'interval', 'cron'], 'default': 'once', 'description': '整图定时'},
            'schedule_config': {'type': 'object', 'description': '调度配置'},
        },
        required=['name', 'nodes'],
        page_rank=30,
        tags=['workflow', 'create', 'graph'],
    ),
    _wcap(
        name='add_node',
        title='增量加节点',
        description='向已有工作流增量加一个节点（task）。',
        scope=_WSCOPE_MANAGE,
        risk_level='medium',
        properties={
            'workflow_id': {'type': 'string', 'minLength': 1},
            'node': {'type': 'object', 'description': '节点声明 {node_key, agent_id?, prompt, ...}'},
        },
        required=['workflow_id', 'node'],
        page_rank=31,
        tags=['workflow', 'node', 'manage'],
    ),
    _wcap(
        name='add_edge',
        title='增量加边',
        description='向已有工作流加一条依赖边（加边时无环校验）。',
        scope=_WSCOPE_MANAGE,
        risk_level='medium',
        properties={
            'workflow_id': {'type': 'string', 'minLength': 1},
            'parent': {'type': 'string', 'minLength': 1},
            'child': {'type': 'string', 'minLength': 1},
        },
        required=['workflow_id', 'parent', 'child'],
        page_rank=32,
        tags=['workflow', 'edge', 'manage'],
    ),
    _wcap(
        name='list_agents',
        title='发现可用分身',
        description='列 owner 可用分身 + 各自专长（编排前映射节点→分身，对齐 kanban Step 0）。',
        scope=_WSCOPE_READ,
        risk_level='low',
        properties={},
        required=[],
        page_rank=33,
        tags=['workflow', 'agents', 'read'],
    ),
    _wcap(
        name='get',
        title='查工作流',
        description='查工作流图 + 节点 + 边 + 最近一次执行各节点状态。',
        scope=_WSCOPE_READ,
        risk_level='low',
        properties={'workflow_id': {'type': 'string', 'minLength': 1}},
        required=['workflow_id'],
        page_rank=34,
        tags=['workflow', 'get', 'read'],
    ),
    _wcap(
        name='get_node_result',
        title='取节点产出',
        description='取某节点本次/最近一次执行的完整产出（§6 深查出口，避免把全图历史塞进 prompt）。',
        scope=_WSCOPE_READ,
        risk_level='low',
        properties={
            'workflow_id': {'type': 'string', 'minLength': 1},
            'node_key': {'type': 'string', 'minLength': 1},
        },
        required=['workflow_id', 'node_key'],
        page_rank=35,
        tags=['workflow', 'node', 'result', 'read'],
    ),
    _wcap(
        name='run',
        title='立即触发整图',
        description='立即触发一次整图执行（一个 workflow_run）。',
        scope=_WSCOPE_RUN,
        risk_level='medium',
        properties={'workflow_id': {'type': 'string', 'minLength': 1}},
        required=['workflow_id'],
        page_rank=36,
        tags=['workflow', 'run', 'trigger'],
    ),
    _wcap(
        name='pause',
        title='暂停工作流',
        description='暂停工作流（active → paused，停止后续定时 fire）。',
        scope=_WSCOPE_MANAGE,
        risk_level='low',
        properties={'workflow_id': {'type': 'string', 'minLength': 1}},
        required=['workflow_id'],
        page_rank=37,
        tags=['workflow', 'pause', 'manage'],
    ),
    _wcap(
        name='cancel',
        title='取消执行/归档',
        description='取消正在跑的整图执行（未完节点 cancelled）或归档工作流（破坏性）。',
        scope=_WSCOPE_MANAGE,
        risk_level='high',
        properties={'workflow_id': {'type': 'string', 'minLength': 1}},
        required=['workflow_id'],
        page_rank=38,
        tags=['workflow', 'cancel', 'manage'],
    ),
    _wcap(
        name='list',
        title='列工作流',
        description='列主人的工作流（含状态/调度/最近执行）。',
        scope=_WSCOPE_READ,
        risk_level='low',
        properties={'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20}},
        required=[],
        page_rank=39,
        tags=['workflow', 'list', 'read'],
    ),
]


_SCHEDULE_PROPS = {
    'schedule_type': {'enum': ['once', 'interval', 'cron'], 'description': '调度类型'},
    'schedule_config': {
        'type': 'object',
        'description': '调度配置：{run_at}（once）/ {minutes}（interval）/ {expr}（cron）',
    },
}

HASN_TASK_AI_NATIVE_MANIFEST = {
    'app_id': 'hasn_task',
    # 「可搜索域目录」：namespace 关键词 → 一句话（云端 tool.search 描述自动汇聚，agent 据此选关键词搜该域工具）。
    'domain_summary': {'task': '任务调度（创建/查询/接续定时任务）', 'workflow': '工作流编排（多任务依赖 DAG）'},
    'version': '1.0.0',
    'workspace_scope': ['personal', 'enterprise'],
    'collaboration_mode': 'workspace_shared',
    'execution_mode': 'local_tool',
    'transport_mode': 'local',
    # 通知：agent 建周期任务 → 「待你确认」提醒卡片（D4，通知非审批票据）；run 完成可发 app 卡。
    'notifications': {
        'emit': {
            'categories': ['app', 'reminder'],
            'card_message': True,
            'display_name': '任务',
        }
    },
    'capabilities': [
        _cap(
            name='create',
            title='建任务',
            description=(
                '替主人创建任务。once 直接生效（scheduled）；agent 建 interval/cron 落 pending_approval '
                '业务态待主人在任务列表确认。幂等键 + 频控（R4）。'
            ),
            scope=_SCOPE_MANAGE,
            risk_level='medium',
            properties={
                'name': {'type': 'string', 'minLength': 1, 'description': '任务名称'},
                'prompt': {'type': 'string', 'minLength': 1, 'description': '任务执行提示'},
                'agent_id': {'type': ['string', 'null'], 'description': '目标分身，缺省=当前分身'},
                **_SCHEDULE_PROPS,
                'risk_level': {
                    'enum': ['low', 'high'],
                    'description': (
                        '风险等级：low=只读/只对主人本人（直接 scheduled 自动跑）；'
                        'high=有外部后果或不可逆（落 pending_approval 等主人批）。缺省由调度类型决定。'
                    ),
                },
                'system_prompt': {'type': ['string', 'null'], 'description': '系统提示词（可选）'},
                'skill_bundle_refs': {'type': 'array', 'description': '市场技能包引用（可选）'},
                'continuation_enabled': {'type': 'boolean', 'default': False, 'description': '任务接续（D2）'},
                'enable_subagents': {'type': 'boolean', 'default': False, 'description': '允许子分身（D5）'},
            },
            required=['name', 'prompt', 'schedule_type', 'schedule_config'],
            page_rank=10,
            tags=['task', 'create', 'schedule'],
        ),
        _cap(
            name='list',
            title='列任务',
            description='列主人的任务（含状态/调度/下次执行）。',
            scope=_SCOPE_READ,
            risk_level='low',
            properties={
                'state': {'type': ['string', 'null'], 'description': '按状态过滤（可选）'},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20},
            },
            required=[],
            page_rank=11,
            tags=['task', 'list', 'read'],
        ),
        _cap(
            name='get',
            title='取任务',
            description='取任务定义详情。',
            scope=_SCOPE_READ,
            risk_level='low',
            properties={'task_id': {'type': 'string', 'minLength': 1}},
            required=['task_id'],
            page_rank=12,
            tags=['task', 'get', 'read'],
        ),
        _cap(
            name='update',
            title='改任务',
            description=(
                '更新任务（局部字段）。R2 守卫：agent 修改调度类字段且结果为 interval/cron → 任务回 '
                'pending_approval（含 once→周期升级）；rejected 任务拒绝任何写。'
            ),
            scope=_SCOPE_MANAGE,
            risk_level='medium',
            properties={
                'task_id': {'type': 'string', 'minLength': 1},
                'name': {'type': ['string', 'null']},
                'prompt': {'type': ['string', 'null']},
                'system_prompt': {'type': ['string', 'null']},
                'schedule_type': {'enum': ['once', 'interval', 'cron', None], 'description': '调度类型（可选）'},
                'schedule_config': {'type': ['object', 'null']},
                'timezone': {'type': ['string', 'null']},
                'continuation_enabled': {'type': ['boolean', 'null']},
                'enable_subagents': {'type': ['boolean', 'null']},
            },
            required=['task_id'],
            page_rank=13,
            tags=['task', 'update', 'manage'],
        ),
        _cap(
            name='pause',
            title='暂停任务',
            description='暂停任务（scheduled → paused）。',
            scope=_SCOPE_MANAGE,
            risk_level='low',
            properties={'task_id': {'type': 'string', 'minLength': 1}},
            required=['task_id'],
            page_rank=14,
            tags=['task', 'pause', 'manage'],
        ),
        _cap(
            name='resume',
            title='恢复任务',
            description='恢复任务。R2 守卫：仅 paused → scheduled；pending_approval/rejected 拒绝。',
            scope=_SCOPE_MANAGE,
            risk_level='low',
            properties={'task_id': {'type': 'string', 'minLength': 1}},
            required=['task_id'],
            page_rank=15,
            tags=['task', 'resume', 'manage'],
        ),
        _cap(
            name='delete',
            title='删任务',
            description='删除任务（软删）。',
            scope=_SCOPE_MANAGE,
            risk_level='high',
            properties={'task_id': {'type': 'string', 'minLength': 1}},
            required=['task_id'],
            page_rank=16,
            tags=['task', 'delete', 'manage'],
        ),
        _cap(
            name='run_now',
            title='立即执行',
            description='立即触发一次执行。R2 守卫：仅 state ∈ {scheduled, paused} 可触发。',
            scope=_SCOPE_RUN,
            risk_level='medium',
            properties={'task_id': {'type': 'string', 'minLength': 1}},
            required=['task_id'],
            page_rank=17,
            tags=['task', 'run', 'trigger'],
        ),
        _cap(
            name='list_runs',
            title='列执行记录',
            description='列某任务的执行记录（倒序）。',
            scope=_SCOPE_READ,
            risk_level='low',
            properties={
                'task_id': {'type': 'string', 'minLength': 1},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 10},
            },
            required=['task_id'],
            page_rank=18,
            tags=['task', 'runs', 'read'],
        ),
        _cap(
            name='get_run',
            title='取单次结果',
            description='取单次 run 结果（output_summary + 产物引用）。',
            scope=_SCOPE_READ,
            risk_level='low',
            properties={'run_id': {'type': 'string', 'minLength': 1}},
            required=['run_id'],
            page_rank=19,
            tags=['task', 'run', 'read'],
        ),
        _cap(
            name='query_results',
            title='查历史结果',
            description=(
                '查任务历史全部结果（接续深查出口，D2）：自动接续只注入上一次摘要，更早历史用本工具按需取。'
                '返回 [{run_id, status, output_summary, artifacts[]}]，产物双引用 local_path/asset_id（D6）。'
            ),
            scope=_SCOPE_READ,
            risk_level='low',
            properties={
                'task_id': {'type': 'string', 'minLength': 1},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'default': 10},
                'status': {'enum': ['success', 'error', None], 'description': '状态过滤（可选）'},
                'include_artifacts': {'type': 'boolean', 'default': True},
            },
            required=['task_id'],
            page_rank=20,
            tags=['task', 'results', 'continuation', 'read'],
        ),
    ],
    # 方案 A：本地工具不进 tools[]（hasn-mcp source=Local，bootstrap 发现）。
    'tools': [],
    'events': [],
    'reverse_invoke': {'supported': False},
    'ui_interfaces': [{'face': 'ui', 'transport': 'daemon_direct'}],
    'publisher': {'developer_id': 'huanxing-first-party', 'publisher_type': 'first_party', 'name': '唤星'},
    'endpoints': {'tool_endpoint': None, 'event_endpoint': None, 'component_origin': 'loopback'},
    'audit': {'fields': list(_AUDIT_FIELDS)},
}

# 多任务编排（工作流）能力随 hasn_task 应用同一 manifest 暴露（节点复用 task；scope workflow:*）。
HASN_TASK_AI_NATIVE_MANIFEST['capabilities'].extend(_WORKFLOW_CAPABILITIES)


def build_hasn_task_app() -> App:
    """构造 hasn_task 的 App（catalog seed 源 + 工作台入口）。

    UI 为 webui 原生路由（``ui_kind=None`` 内联导航至 ``/tasks``，同 knowledge/community），
    非 sidecar、非独立窗口；execution_mode=local_tool（本地工具驱动 + 原生 UI）。
    """
    from backend.app.hasn.service.app_catalog_registry import App

    return App(
        id='hasn_task',
        name='任务',
        icon='brand-task',
        description='把要做的事交给分身按计划执行，结果自动带回、可追溯——你只管验收，省心不掉事。',
        scope=('personal', 'enterprise'),
        collaboration_mode='workspace_shared',
        entry_route='/apps/tasks',
        install_policy='auto',
        execution_mode='local_tool',
    )

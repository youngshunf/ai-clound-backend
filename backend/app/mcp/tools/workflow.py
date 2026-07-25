"""平台工具 · workflow 域（多任务编排 DAG，模块 12，节点复用 task）。

把分身的「纯云端代理」工作流工具从 hasn-node 本地 hasn-mcp 迁到**云端 platform MCP 工具**
（不依赖本地操作的工具一律走云端，与 plan/task 同范式，TOOLMIG2）：分身经
`/api/v1/mcp/streamable` 直达云端，工具体直调云端权威 `agent_workflow_service`（in-process，
**不再**经 daemon → `/api/v1/hasn-task/agent/*` HTTP relay）。owner 隔离由 Agent JWT/MCP Key
解析出的 `agent_context.owner_hasn_id` 强制，身份绝不入请求体。

执行侧不变：整图 fire/调度仍由本地/云端 Runtime Host 基于 sync mirror 自行 tick（中心不 tick），
run/pause/cancel 只写云端权威状态信号，由持有 driver 的节点本地 WorkflowScheduler 落地。

- 工具名 + input_schema 与 `ai_native_manifest._WORKFLOW_CAPABILITIES` 1:1（workflow_id = workflow_uuid）。
- **不暴露 add_node/add_edge**：agent 补线（agent_workflow_service）只支持经 create 一次声明整图，
  增量改图不在 agent 工具面（与本地 hasn-mcp 一致）。approve/reject 是主人侧 D4，不作 agent 工具。
- 三态闸门由 `server.call_tool` 统一判定（维度①，D3 活取），工具体不二次校验。
- scope 与本地 hasn-mcp `workflow.rs` 跨仓对齐（`test_local_tool_scope_alignment`）：读类无 scope；
  建/暂停/取消 = `workflow:manage`；触发 = `workflow:run`。
- 写类经 `async_db_session.begin()` 自动提交（service 只 flush）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from backend.app.hasn_task.service.agent_workflow_service import agent_workflow_service
from backend.app.hasn_task.service.workflow_template_service import workflow_template_service
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

NAMESPACE = 'hasn.workflow'
SCOPE_MANAGE = 'workflow:manage'
SCOPE_RUN = 'workflow:run'

Handler = Callable[[Any, AgentContext, dict[str, Any]], Awaitable[Any]]


# ── handlers（读：owner-scoped；create 用完整 AgentTokenPayload；状态信号用 owner_id）─────
async def _h_create(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """建图（W2/D4）：节点缺省 agent=发起分身；定时图 → pending_approval。service 需完整凭据。"""
    return await agent_workflow_service.create_workflow(db, agent=ctx.to_token_payload(), params=args)


async def _h_list_agents(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_workflow_service.list_agents(db, owner_id=ctx.owner_hasn_id)


async def _h_get(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_workflow_service.get_workflow(db, owner_id=ctx.owner_hasn_id, workflow_uuid=str(args['workflow_id']))


async def _h_get_node_result(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_workflow_service.get_node_result(
        db, owner_id=ctx.owner_hasn_id, workflow_uuid=str(args['workflow_id']), node_key=str(args['node_key'])
    )


async def _h_run_artifacts(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    # doc36 §6.2 零入参：缺 workflow_run_uuid 时经当前会话（_hasn_session_id → ctx.session_id）反查所属 run。
    run_uuid = args.get('workflow_run_uuid')
    return await agent_workflow_service.run_artifacts(
        db,
        owner_id=ctx.owner_hasn_id,
        session_id=ctx.session_id,
        workflow_run_uuid=str(run_uuid) if run_uuid else None,
    )


async def _h_list(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    # project_id 只认显式入参，**不从 ContextVar 继承**：doc11 §4.0 的继承链管的是「新东西挂到哪个项目」
    # （写语义），不该拿来悄悄裁剪「能看见什么」（读语义）——同一句 list 因为看不见的上下文返回不同
    # 结果，分身无从判断列表是否完整。要按项目筛就显式给 project_id。
    project_id = args.get('project_id')
    return await agent_workflow_service.list_workflows(
        db, owner_id=ctx.owner_hasn_id, project_id=str(project_id) if project_id else None
    )


async def _h_run(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_workflow_service.run(db, owner_id=ctx.owner_hasn_id, workflow_uuid=str(args['workflow_id']))


async def _h_pause(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_workflow_service.pause(db, owner_id=ctx.owner_hasn_id, workflow_uuid=str(args['workflow_id']))


async def _h_cancel(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_workflow_service.cancel(db, owner_id=ctx.owner_hasn_id, workflow_uuid=str(args['workflow_id']))


# ── schema 片段（与 manifest _WORKFLOW_CAPABILITIES 1:1）──────────────────────────
_NODE_SPEC = {
    'type': 'array',
    'description': (
        '节点列表 [{node_key, agent_id?, prompt, system_prompt?, '
        'skill_bundle_refs?, enabled_toolsets?, enable_subagents?}]'
    ),
}
_EDGE_SPEC = {'type': 'array', 'description': '依赖边列表 [{parent, child}]（建图时无环校验）'}


# ── 工具规格（action → name=hasn.workflow.<action>）────────────────────────────────
_SPECS: list[dict[str, Any]] = [
    {
        'action': 'create',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_create,
        'desc': (
            '一次声明整图（节点+依赖边），把大任务拆成多个可并行/交叉/依赖的子任务。节点可跨分身。'
            'agent 建带定时的工作流 → pending_approval 业务态待主人确认；一次性直接可跑。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'minLength': 1, 'description': '工作流名称'},
                'goal': {'type': ['string', 'null'], 'description': '总目标（整图验收口径）'},
                'nodes': _NODE_SPEC,
                'edges': _EDGE_SPEC,
                'schedule_type': {'enum': ['once', 'interval', 'cron'], 'description': '整图定时（默认 once）'},
                'schedule_config': {'type': 'object', 'description': '调度配置'},
            },
            'required': ['name', 'nodes'],
        },
    },
    {
        'action': 'list_agents',
        'write': False,
        'scopes': [],
        'handler': _h_list_agents,
        'desc': '列 owner 可用分身 + 各自专长（编排前映射节点→分身，对齐 kanban Step 0）。确定性读。',
        'schema': {'type': 'object', 'properties': {}},
    },
    {
        'action': 'get',
        'write': False,
        'scopes': [],
        'handler': _h_get,
        'desc': '查工作流图 + 节点 + 边 + 最近一次执行各节点状态。确定性读。',
        'schema': {
            'type': 'object',
            'properties': {'workflow_id': {'type': 'string', 'minLength': 1}},
            'required': ['workflow_id'],
        },
    },
    {
        'action': 'get_node_result',
        'write': False,
        'scopes': [],
        'handler': _h_get_node_result,
        'desc': '取某节点本次/最近一次执行的完整产出（§6 深查出口，避免把全图历史塞进 prompt）。确定性读。',
        'schema': {
            'type': 'object',
            'properties': {
                'workflow_id': {'type': 'string', 'minLength': 1},
                'node_key': {'type': 'string', 'minLength': 1},
            },
            'required': ['workflow_id', 'node_key'],
        },
    },
    {
        'action': 'run_artifacts',
        'write': False,
        'scopes': [],
        'handler': _h_run_artifacts,
        'desc': (
            '拿本次工作流执行（run）全部节点的产物清单——**任何一环都能调**：要看上游几环产了什么、'
            '要跨环复用前面的产出，用它一次拿全（不只是末环出「成果总览」时才用）。零入参：'
            '不传 workflow_run_uuid 时，服务端据当前工作会话反查本次 run。返回按拓扑序（第几步）排列的节点，'
            '每个节点带其产物 [{artifact_id, title, uri, resource_kind, source_app_id, created_time}]（uri 即 '
            'artifact.list 里的 resource_uri；写总览时链接用 uri 原值，勿自行拼 URI）。产物只含最新版本，'
            '每节点上限 50，超限置 artifacts_truncated=true。确定性读。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                'workflow_run_uuid': {
                    'type': ['string', 'null'],
                    'description': '可选：显式指定要查的执行实例（主人 UI / 非节点会话用）；缺省由当前会话反查',
                },
            },
        },
    },
    {
        'action': 'run',
        'write': True,
        'scopes': [SCOPE_RUN],
        'handler': _h_run,
        'desc': '立即触发一次整图执行（一个 workflow_run）。仅 status ∈ {active, paused} 可触发。',
        'schema': {
            'type': 'object',
            'properties': {'workflow_id': {'type': 'string', 'minLength': 1}},
            'required': ['workflow_id'],
        },
    },
    {
        'action': 'pause',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_pause,
        'desc': '暂停工作流（active → paused，停止后续定时 fire）。',
        'schema': {
            'type': 'object',
            'properties': {'workflow_id': {'type': 'string', 'minLength': 1}},
            'required': ['workflow_id'],
        },
    },
    {
        'action': 'cancel',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_cancel,
        'desc': '取消正在跑的整图执行（未完节点 cancelled）。破坏性。',
        'schema': {
            'type': 'object',
            'properties': {'workflow_id': {'type': 'string', 'minLength': 1}},
            'required': ['workflow_id'],
        },
    },
    {
        'action': 'list',
        'write': False,
        'scopes': [],
        'handler': _h_list,
        'desc': '列主人的工作流（含状态/调度/最近执行）。确定性读。给 project_id 则只列该项目下的。',
        'schema': {
            'type': 'object',
            'properties': {
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'description': '默认 20'},
                'project_id': {
                    'type': 'string',
                    'description': '按所属平台项目筛选（hasn_project.id 云端权威 id）；不给则列全部',
                },
            },
        },
    },
]


class _WorkflowTool(BaseTool):
    """workflow 域单 struct + spec 派发（对齐 plan.py/task.py，Rust WorkflowOp 枚举派发）。"""

    def __init__(self, spec: dict[str, Any]) -> None:
        self._action = spec['action']
        self._name = f'{NAMESPACE}.{spec["action"]}'
        self._desc = spec['desc']
        self._input_schema = spec['schema']
        self._write = bool(spec['write'])
        self._scopes: list[str] = list(spec['scopes'])
        self._handler: Handler = spec['handler']

    @property
    def source(self) -> str:
        return 'platform'

    @property
    def name(self) -> str:
        return self._name

    @property
    def namespace(self) -> str:
        return NAMESPACE

    @property
    def execution_location(self) -> str:
        return 'cloud'

    @property
    def description(self) -> str:
        return self._desc

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._input_schema

    @property
    def required_scopes(self) -> list[str]:
        # 读类无 scope；管理类 workflow:manage；触发类 workflow:run（跨仓与本地 hasn-mcp 对齐）。
        return list(self._scopes)

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        # 维度① 三态由 server.call_tool 统一判定（D3），工具内不二次校验。
        if self._write:
            async with async_db_session.begin() as db:
                return await self._handler(db, agent_context, arguments)
        async with async_db_session() as db:
            return await self._handler(db, agent_context, arguments)


WORKFLOW_TOOLS: list[_WorkflowTool] = [_WorkflowTool(spec) for spec in _SPECS]


# ══════════════════════ 工作流模板子域（hasn.workflow.template.*，P5-cloud doc94 §10-P5）══════════════════════
# 「场景即模板」：分身采访主人 → draft 蓝图（过 §6.3 校验）→ 主人画廊可见草稿 → publish 上架 → instantiate
# 据模板建 cloud 权威 workflow。工具名 4 段（namespace=hasn.workflow，action=template.*），复用 _WorkflowTool
# 派发；wrap workflow_template_service（instantiate 内部再 wrap agent_workflow_service.create_workflow）。
# scope 与 workflow 执行工具同键（读无 scope / 建管 workflow:manage / 实例化 workflow:run）——不引入新 scope 键，
# 故不破坏跨仓 scope 对齐守卫（test_local_tool_scope_alignment 比的是 scope 键集合）。

# graph_spec 蓝图 schema 片段（canonical 键形见 doc11 §4.3；分身自撰产物，允许内联）
_GRAPH_SPEC_SCHEMA = {
    'type': 'object',
    'description': (
        '图蓝图 {nodes:[...], edges:[...]}。node：node_key(唯一)/name/node_kind(origin|agent)/'
        'is_origin(bool)/description/default_agent_type/apps[]/skills[]/prompt/system_prompt/'
        'output_spec{required,label?,expects:[...]}（expects 每条二选一：应用资源写 resource_kind '
        '如 knowledge.base/deck.presentation，非应用资源写 artifact_kind∈document|image|video|voice|file；'
        '多条之间是「或」。写错即报错，不静默放行）/'
        'review_policy{mode,criteria,reviewer_agent_type,max_rejects}/'
        'display{order,step_label}；edge：{parent, child}（DAG 无环，至少一个 is_origin 起点）。'
    ),
    'properties': {
        'nodes': {'type': 'array', 'description': '节点列表（见上）'},
        'edges': {'type': 'array', 'description': '依赖边列表 [{parent, child}]'},
    },
}


async def _h_tpl_draft(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await workflow_template_service.draft_template(db, owner_id=ctx.owner_hasn_id, params=args)


async def _h_tpl_update(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await workflow_template_service.update_template(
        db, owner_id=ctx.owner_hasn_id, template_key=str(args['template_key']), params=args
    )


async def _h_tpl_get(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await workflow_template_service.get_template(
        db, owner_id=ctx.owner_hasn_id, template_key=str(args['template_key'])
    )


async def _h_tpl_list(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await workflow_template_service.list_templates(
        db, owner_id=ctx.owner_hasn_id, domain=args.get('domain'), status=args.get('status')
    )


async def _h_tpl_instantiate(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    # 需完整凭据（agent_hasn_id/owner/session）建 cloud workflow，身份绝不入请求体。
    return await workflow_template_service.instantiate_template(
        db, agent=ctx.to_token_payload(), template_key=str(args['template_key']), params=args
    )


async def _h_tpl_publish(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await workflow_template_service.publish_template(
        db, owner_id=ctx.owner_hasn_id, template_key=str(args['template_key'])
    )


_TEMPLATE_SPECS: list[dict[str, Any]] = [
    {
        'action': 'template.draft',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_tpl_draft,
        'desc': (
            '提交工作流模板草案（graph_spec 全量蓝图）。服务端过 §6.3 校验（图合法/引用合法/节点上限）后存为草稿，'
            '主人可在画廊看到。校验失败会指名到具体 node_key/edge/app/kind，据 message 修正后重试。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'minLength': 1, 'description': '模板展示名'},
                'graph_spec': _GRAPH_SPEC_SCHEMA,
                'domain': {'type': ['string', 'null'], 'description': '领域分组 code（startup/finance/...；非空=场景模板）'},
                'tagline': {'type': ['string', 'null'], 'description': '一句话卖点（画廊卡短语）'},
                'description': {'type': ['string', 'null'], 'description': '链路详述'},
                'icon': {'type': ['string', 'null'], 'description': '图标 key（lucide kebab 名）'},
                'accent': {'type': ['string', 'null'], 'description': '主题强调色（brand/teal/indigo/rose）'},
                'sort_order': {'type': 'integer', 'description': '展示排序（默认 0）'},
            },
            'required': ['name', 'graph_spec'],
        },
    },
    {
        'action': 'template.update',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_tpl_update,
        'desc': (
            '更新自己名下 draft/active 模板（version+1），过同套 §6.3 校验。'
            '只能改自己创建的模板；内置模板 / 别人的模板拒绝。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                'template_key': {'type': 'string', 'minLength': 1, 'description': '要更新的模板键'},
                'graph_spec': _GRAPH_SPEC_SCHEMA,
                'name': {'type': ['string', 'null']},
                'domain': {'type': ['string', 'null']},
                'tagline': {'type': ['string', 'null']},
                'description': {'type': ['string', 'null']},
                'icon': {'type': ['string', 'null']},
                'accent': {'type': ['string', 'null']},
                'sort_order': {'type': ['integer', 'null']},
            },
            'required': ['template_key'],
        },
    },
    {
        'action': 'template.get',
        'write': False,
        'scopes': [],
        'handler': _h_tpl_get,
        'desc': '读模板详情（含 graph_spec 全量蓝图）。可见范围：自己名下 + 内置。确定性读。',
        'schema': {
            'type': 'object',
            'properties': {'template_key': {'type': 'string', 'minLength': 1}},
            'required': ['template_key'],
        },
    },
    {
        'action': 'template.list',
        'write': False,
        'scopes': [],
        'handler': _h_tpl_list,
        'desc': '列可见模板（自己名下 + 内置），可按 domain/status 过滤。列表不含 graph_spec（详情才带）。确定性读。',
        'schema': {
            'type': 'object',
            'properties': {
                'domain': {'type': ['string', 'null'], 'description': '按领域过滤（可选）'},
                'status': {'type': ['string', 'null'], 'description': '按状态过滤 draft/active/...（可选）'},
            },
        },
    },
    {
        'action': 'template.instantiate',
        'write': True,
        'scopes': [SCOPE_RUN],
        'handler': _h_tpl_instantiate,
        'desc': (
            '据模板实例化一条 cloud 权威工作流：读模板蓝图 → 建 workflow（节点缺省=发起分身）→ 返 workflow 引用。'
            '起点输入 origin_input 作为锚点，node_overrides 可逐节点定制 prompt/agent_id。付费模板本期免判直接放行。'
            '⚠️ 场景必须挂到一个平台项目下：显式传 project_id，或经工作会话上下文继承；两者都无会返 PROJECT_REQUIRED，'
            '此时回头问主人「这个场景挂到哪个项目下」。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                'template_key': {'type': 'string', 'minLength': 1, 'description': '要实例化的模板键'},
                'project_id': {
                    'type': ['string', 'null'],
                    'description': '所属平台项目 id（云端权威 id）。场景必填；缺省则从工作会话上下文继承，都无则 PROJECT_REQUIRED。',
                },
                'title': {'type': ['string', 'null'], 'description': '实例工作流名称（缺省取模板名）'},
                'goal': {'type': ['string', 'null'], 'description': '实例总目标（缺省取模板描述）'},
                'origin_input': {'type': ['string', 'null'], 'description': '起点输入（主人锚点，如想法/研究命题）'},
                'node_overrides': {
                    'type': 'object',
                    'description': '逐节点定制覆盖 {node_key: {prompt?, agent_id?, system_prompt?}}',
                },
            },
            'required': ['template_key'],
        },
    },
    {
        'action': 'template.publish',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_tpl_publish,
        'desc': (
            '上架自己名下模板到市场：过 §6.3 校验 → status 转 active + version 快照 + market_ref 占位。'
            '外发+动钱语义，需主人确认（ask 闸）。完整 listing/定价归后续。'
        ),
        'schema': {
            'type': 'object',
            'properties': {'template_key': {'type': 'string', 'minLength': 1}},
            'required': ['template_key'],
        },
    },
]


# 模板工具复用 _WorkflowTool 派发（namespace=hasn.workflow，name=hasn.workflow.template.<action>）。
# 独立成 list（不并入 WORKFLOW_TOOLS）——WORKFLOW_TOOLS 有精确名集守卫（test_workflow_tools.py），
# 模板子域自带守卫，二者互不干扰；server.py 两 list 一并注册。
WORKFLOW_TEMPLATE_TOOLS: list[_WorkflowTool] = [_WorkflowTool(spec) for spec in _TEMPLATE_SPECS]

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

from typing import Any, Awaitable, Callable

from backend.app.hasn_task.service.agent_workflow_service import agent_workflow_service
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


async def _h_list(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_workflow_service.list_workflows(db, owner_id=ctx.owner_hasn_id)


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
        'desc': '列主人的工作流（含状态/调度/最近执行）。确定性读。',
        'schema': {
            'type': 'object',
            'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'description': '默认 20'}},
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

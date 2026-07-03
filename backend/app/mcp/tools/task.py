"""平台工具 · task 域（任务调度，模块 12）。

把分身的「纯云端代理」任务工具从 hasn-node 本地 hasn-mcp 迁到**云端 platform MCP 工具**
（不依赖本地操作的工具一律走云端，与 plan/designsystem 同范式，TOOLMIG2）：分身经
`/api/v1/mcp/streamable` 直达云端，工具体直调云端权威 `agent_task_service`（in-process，
**不再**经 daemon → `/api/v1/hasn-task/agent/*` HTTP relay）。owner 隔离由 Agent JWT/MCP Key
解析出的 `agent_context.owner_hasn_id` 强制，身份绝不入请求体。

执行侧不变：任务 fire/调度仍由本地/云端 Runtime Host 基于 task sync mirror 自行 tick
（中心不 tick），本工具只承载「定义读写 + 触发」控制面（与 agent REST 端点同一 service）。

- 工具名 + input_schema 与 `ai_native_manifest.HASN_TASK_AI_NATIVE_MANIFEST.capabilities`
  逐字段 1:1（task_id = 端云稳定 task_uuid）。
- 三态闸门由 `server.call_tool` 统一判定（维度①，D3 活取），工具体不二次校验。
- scope 与本地 hasn-mcp `task.rs` 跨仓对齐（`test_local_tool_scope_alignment`）：读类无 scope；
  建/改/暂停/恢复/删 = `task:manage`；立即执行 = `task:run`。
- 写类经 `async_db_session.begin()` 自动提交；WSPUSH `tasks` 失效由 service 的
  `_emit_task_event` → `bump_owner(KIND_TASKS)` 自带（让 owner 在线节点刷新本地镜像），工具体不重复发。
- approve/reject 是主人侧 D4 业务态（owner JWT 经 app 面），**不**作为 agent 工具暴露。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from backend.app.hasn_task.service.agent_task_service import agent_task_service, task_to_public
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

NAMESPACE = 'hasn.task'
SCOPE_MANAGE = 'task:manage'
SCOPE_RUN = 'task:run'

Handler = Callable[[Any, AgentContext, dict[str, Any]], Awaitable[Any]]


# ── helpers ──────────────────────────────────────────────────────────────────
def _without(args: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in args.items() if k not in keys}


# ── handlers（读：owner-scoped；写：建/改用完整 AgentTokenPayload，状态转换用 owner_id）─────
async def _h_create(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """建任务（D4/R4）：service 需完整凭据（发待确认提醒用 agent_name）。返回任务投影 + 是否新建。"""
    public, is_new = await agent_task_service.create_task(db, agent=ctx.to_token_payload(), params=args)
    return {**public, 'created': is_new}


async def _h_update(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_task_service.update_task(
        db, agent=ctx.to_token_payload(), task_uuid=str(args['task_id']), patch=_without(args, 'task_id')
    )


async def _h_list(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_task_service.list_tasks(
        db, owner_id=ctx.owner_hasn_id, state=args.get('state'), limit=int(args.get('limit') or 20)
    )


async def _h_get(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    row = await agent_task_service.get_task_row(db, owner_id=ctx.owner_hasn_id, task_uuid=str(args['task_id']))
    return task_to_public(row)


async def _h_pause(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_task_service.pause_task(db, owner_id=ctx.owner_hasn_id, task_uuid=str(args['task_id']))


async def _h_resume(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_task_service.resume_task(db, owner_id=ctx.owner_hasn_id, task_uuid=str(args['task_id']))


async def _h_run_now(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_task_service.run_now(db, owner_id=ctx.owner_hasn_id, task_uuid=str(args['task_id']))


async def _h_delete(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    await agent_task_service.delete_task(db, owner_id=ctx.owner_hasn_id, task_uuid=str(args['task_id']))
    return {'deleted': True}


async def _h_list_runs(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_task_service.list_run_summaries(
        db, owner_id=ctx.owner_hasn_id, task_uuid=str(args['task_id']), limit=int(args.get('limit') or 10)
    )


async def _h_get_run(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_task_service.get_run_summary(db, owner_id=ctx.owner_hasn_id, run_uuid=str(args['run_id']))


async def _h_query_results(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await agent_task_service.list_run_summaries(
        db,
        owner_id=ctx.owner_hasn_id,
        task_uuid=str(args['task_id']),
        limit=int(args.get('limit') or 10),
        status=args.get('status'),
        include_artifacts=bool(args.get('include_artifacts', True)),
    )


# ── schema 片段（与 manifest capabilities 1:1）────────────────────────────────
_SCHEDULE_PROPS = {
    'schedule_type': {'enum': ['once', 'interval', 'cron'], 'description': '调度类型'},
    'schedule_config': {
        'type': 'object',
        'description': '调度配置：{run_at}（once）/ {minutes}（interval）/ {expr}（cron）',
    },
}


# ── 工具规格（action → name=hasn.task.<action>）────────────────────────────────
_SPECS: list[dict[str, Any]] = [
    {
        'action': 'create',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_create,
        'desc': (
            '替主人创建任务。once 直接生效（scheduled）；agent 建 interval/cron 落 pending_approval '
            '业务态待主人在任务列表确认。幂等键 + 频控（R4）。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'minLength': 1, 'description': '任务名称'},
                'prompt': {'type': 'string', 'minLength': 1, 'description': '任务执行提示'},
                'agent_id': {'type': ['string', 'null'], 'description': '目标分身，缺省=当前分身'},
                **_SCHEDULE_PROPS,
                'risk_level': {
                    'enum': ['low', 'high'],
                    'description': (
                        '风险等级：low=只读/只对主人本人（提醒/汇报/整理/查询，直接 scheduled 自动跑）；'
                        'high=有外部后果或不可逆（外发/花钱/改外部数据，落 pending_approval 等主人批）。'
                        '缺省时由调度类型决定（周期任务待主人确认）。'
                    ),
                },
                'system_prompt': {'type': ['string', 'null'], 'description': '系统提示词（可选）'},
                'skill_bundle_refs': {'type': 'array', 'description': '市场技能包引用（可选）'},
                'continuation_enabled': {'type': 'boolean', 'description': '任务接续（D2）'},
                'enable_subagents': {'type': 'boolean', 'description': '允许子分身（D5）'},
            },
            'required': ['name', 'prompt', 'schedule_type', 'schedule_config'],
        },
    },
    {
        'action': 'list',
        'write': False,
        'scopes': [],
        'handler': _h_list,
        'desc': '列主人的任务（含状态/调度/下次执行）。确定性读。',
        'schema': {
            'type': 'object',
            'properties': {
                'state': {'type': ['string', 'null'], 'description': '按状态过滤（可选）'},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'description': '默认 20'},
            },
        },
    },
    {
        'action': 'get',
        'write': False,
        'scopes': [],
        'handler': _h_get,
        'desc': '取任务定义详情。确定性读。',
        'schema': {
            'type': 'object',
            'properties': {'task_id': {'type': 'string', 'minLength': 1, 'description': '任务 id（task_uuid）'}},
            'required': ['task_id'],
        },
    },
    {
        'action': 'update',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_update,
        'desc': (
            '更新任务（局部字段）。R2 守卫：agent 修改调度类字段且结果为 interval/cron → 任务回 '
            'pending_approval（含 once→周期升级）；rejected 任务拒绝任何写。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
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
            'required': ['task_id'],
        },
    },
    {
        'action': 'pause',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_pause,
        'desc': '暂停任务（scheduled → paused）。',
        'schema': {
            'type': 'object',
            'properties': {'task_id': {'type': 'string', 'minLength': 1}},
            'required': ['task_id'],
        },
    },
    {
        'action': 'resume',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_resume,
        'desc': '恢复任务。R2 守卫：仅 paused → scheduled；pending_approval/rejected 拒绝。',
        'schema': {
            'type': 'object',
            'properties': {'task_id': {'type': 'string', 'minLength': 1}},
            'required': ['task_id'],
        },
    },
    {
        'action': 'delete',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_delete,
        'desc': '删除任务（软删）。',
        'schema': {
            'type': 'object',
            'properties': {'task_id': {'type': 'string', 'minLength': 1}},
            'required': ['task_id'],
        },
    },
    {
        'action': 'run_now',
        'write': True,
        'scopes': [SCOPE_RUN],
        'handler': _h_run_now,
        'desc': '立即触发一次执行。R2 守卫：仅 state ∈ {scheduled, paused} 可触发。',
        'schema': {
            'type': 'object',
            'properties': {'task_id': {'type': 'string', 'minLength': 1}},
            'required': ['task_id'],
        },
    },
    {
        'action': 'list_runs',
        'write': False,
        'scopes': [],
        'handler': _h_list_runs,
        'desc': '列某任务的执行记录（倒序）。确定性读。',
        'schema': {
            'type': 'object',
            'properties': {
                'task_id': {'type': 'string', 'minLength': 1},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'description': '默认 10'},
            },
            'required': ['task_id'],
        },
    },
    {
        'action': 'get_run',
        'write': False,
        'scopes': [],
        'handler': _h_get_run,
        'desc': '取单次 run 结果（output_summary + 产物引用）。确定性读。',
        'schema': {
            'type': 'object',
            'properties': {'run_id': {'type': 'string', 'minLength': 1}},
            'required': ['run_id'],
        },
    },
    {
        'action': 'query_results',
        'write': False,
        'scopes': [],
        'handler': _h_query_results,
        'desc': (
            '查任务历史全部结果（接续深查出口，D2）：自动接续只注入上一次摘要，更早历史用本工具按需取。'
            '返回 [{run_id, status, output_summary, artifacts[]}]，产物双引用 local_path/asset_id（D6）。'
        ),
        'schema': {
            'type': 'object',
            'properties': {
                'task_id': {'type': 'string', 'minLength': 1},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50, 'description': '默认 10'},
                'status': {'enum': ['success', 'error', None], 'description': '状态过滤（可选）'},
                'include_artifacts': {'type': 'boolean', 'description': '默认 true'},
            },
            'required': ['task_id'],
        },
    },
]


class _TaskTool(BaseTool):
    """task 域单 struct + spec 派发（避免同形态类样板，对齐 plan.py / Rust TaskOp 枚举派发）。"""

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
        # 读类无 scope；管理类 task:manage；触发类 task:run（跨仓与本地 hasn-mcp 对齐）。
        return list(self._scopes)

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        # 维度① 三态由 server.call_tool 统一判定（D3），工具内不二次校验。
        if self._write:
            # service 写方法只 flush 不 commit → 用 .begin() 自动提交；WSPUSH 失效 service 内部自带。
            async with async_db_session.begin() as db:
                return await self._handler(db, agent_context, arguments)
        async with async_db_session() as db:
            return await self._handler(db, agent_context, arguments)


TASK_TOOLS: list[_TaskTool] = [_TaskTool(spec) for spec in _SPECS]

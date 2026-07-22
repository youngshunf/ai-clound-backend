"""平台工具 · plan 域（规划与目标管理，模块 19）。

把分身的「纯云端代理」规划工具从 hasn-node 本地 hasn-mcp 迁到**云端 platform MCP 工具**
（不需要操作本地文件/数据的工具一律走云端，与 contact/message 同范式）：分身经
`/api/v1/mcp/streamable` 直达云端，工具体直调云端权威 `plan_service`（in-process，
**不再**经 daemon → `/api/v1/plan/agent/*` HTTP relay）。owner 隔离由 Agent JWT/MCP Key
解析出的 `agent_context.owner_hasn_id` 强制，身份绝不入请求体。

迁移范围 = 全部 PURE_RELAY（capture/triage/today + goal/project/todo/event/habit 五对象 CRUD
+ preference）。**保留在本地 hasn-mcp** 的是有真本地编排/计算的：decompose/briefing/review
（复合编排）、schedule/reschedule（本地纯函数排程引擎）、delegate（daemon 工作会话网关）、
validate（纯本地自检）。

- 工具名 + input_schema 与原 hasn-mcp 工具**逐字段 1:1**（分身/技能引用不变）；priority 仍声明
  string（service `_coerce_priority` 归一化为 SMALLINT），id/外键声明 integer。
- 三态闸门由 `server.call_tool` 统一判定（维度①，D3 活取），工具体不二次校验。
- 写类经 `async_db_session.begin()` 自动提交，并 best-effort 发 WSPUSH `plan` 失效
  （对齐 agent REST 端点的 `_bump_plan_sync`，让 owner 在线节点刷新本地镜像）。
"""

from __future__ import annotations

import logging

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from backend.app.hasn.schema.resource_descriptor import ArtifactRegistration
from backend.app.hasn_plan.service import (
    resource_adapter as _plan_resource_adapter,  # noqa: F401  # G6 使用点注册兜底：注册 plan_event adapter（ai_native_app_registry 未 import 本模块，靠此在启动装配工具时注册）
)
from backend.app.hasn_plan.service.plan_app_service import plan_service
from backend.app.hasn_plan.service.plan_authz import ERR_NOT_IN_ENTERPRISE_SPACE, resolve_plan_write_scope
from backend.app.hasn_plan.service.plan_notify import notify_invited
from backend.app.mcp.artifact_registration import merge_resource_uri, register_app_resource_artifact
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.base import BaseTool
from backend.database.db import async_db_session

logger = logging.getLogger(__name__)

NAMESPACE = 'hasn.plan'
SCOPE_WRITE = 'plan:write'
SCOPE_MANAGE = 'plan:manage'  # 企业会议协同（invite/rsvp，PLAN-ENT [04] §6.2）
SCOPE_READ = 'plan:read'  # 跨成员忙闲读（availability，受 A3 可见性约束）

# 产物标题兜底：result 没带 title 时用的资源名词（与 manifest resources[].card.verb 同义）。
_PLAN_REF_TYPE_LABELS = {'goal': '目标', 'plan': '计划'}

Handler = Callable[[Any, AgentContext, dict[str, Any]], Awaitable[Any]]


# ── helpers ──────────────────────────────────────────────────────────────────
def _parse_dt(value: Any) -> datetime | None:
    """RFC3339 字符串 → datetime（None/空串 → None；已是 datetime 原样）。"""
    if not value:  # None 或空串（datetime 恒真值，不会误判）
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _without(args: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in args.items() if k not in keys}


def _require_owner_hasn_id(ctx: AgentContext) -> str:
    """从已鉴权分身上下文取得主人身份，缺失时拒绝访问主人隔离数据。"""
    owner_hasn_id = ctx.owner_hasn_id
    if not owner_hasn_id:
        raise RuntimeError('plan: Agent 凭证缺少 owner_hasn_id')
    return owner_hasn_id


async def _safe_bump(db: Any, owner_hasn_id: str | None) -> None:
    """写点后 → WSPUSH ``hasn.sync.invalidate(plan)`` 给该 owner 在线节点（best-effort，绝不阻断提交）。"""
    if not owner_hasn_id:
        return
    try:
        from backend.app.hasn.service import sync_invalidate_service as siv

        await siv.bump_owner(siv.KIND_PLAN, db, owner_hasn_id)
    except Exception as e:
        logger.warning('[plan] platform tool sync invalidate 推送失败 (非致命): %s', e)


# ── PE-7 写类空间入参解析 + 企业归属注入（[04] §6.1）───────────────────────────────
def _as_list(v: Any) -> list[str]:
    """入参归一成 hasn_id 字符串列表（None→[]，单值→[单值]，去空白/去空串）。"""
    if v is None:
        return []
    items = v if isinstance(v, list) else [v]
    return [s for s in (str(x).strip() for x in items) if s]


async def _resolve_write_scope(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """PE-7：从 ``scope`` 入参 + 异步快照（daemon 注入的 ``_active_enterprise_id``）解析企业归属。

    返回 ``PlanWriteScope``；``scope=enterprise`` 但不在企业空间 → ``ok=False``（诚实拒绝，见 ``_reject``）。
    """
    return await resolve_plan_write_scope(
        db,
        owner_hasn_id=_require_owner_hasn_id(ctx),
        owner_user_id=ctx.owner_user_id,
        scope=args.get('scope'),
        snapshot_enterprise_id=args.get('_active_enterprise_id'),
    )


def _reject_not_in_enterprise() -> dict[str, Any]:
    """PE-7 诚实拒绝：scope=enterprise 但当前不在企业空间——不写、不切换。"""
    return {
        'ok': False,
        'error_code': ERR_NOT_IN_ENTERPRISE_SPACE,
        'message': '当前不在企业空间，无法创建企业条目。请先切换到对应企业空间后重试，或用 scope=personal 建个人条目。',
    }


# ── handler 工厂（service 方法签名异构，按形态分几类）────────────────────────────
def _h_list(method: str, *arg_keys: str) -> Handler:
    async def handler(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
        kwargs = {k: args.get(k) for k in arg_keys}
        return await getattr(plan_service, method)(db, owner=_require_owner_hasn_id(ctx), **kwargs)

    return handler


def _h_get(method: str) -> Handler:
    async def handler(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
        return await getattr(plan_service, method)(db, owner=_require_owner_hasn_id(ctx), pk=int(args['id']))

    return handler


def _h_create(method: str) -> Handler:
    async def handler(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
        return await getattr(plan_service, method)(db, owner=_require_owner_hasn_id(ctx), data=args)

    return handler


def _h_update(method: str) -> Handler:
    async def handler(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
        return await getattr(plan_service, method)(
            db, owner=_require_owner_hasn_id(ctx), pk=int(args['id']), data=_without(args, 'id')
        )

    return handler


def _h_delete(method: str) -> Handler:
    async def handler(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
        await getattr(plan_service, method)(db, owner=_require_owner_hasn_id(ctx), pk=int(args['id']))
        return {'deleted': True}

    return handler


def _h_create_child(method: str, parent_key: str, parent_param: str) -> Handler:
    """子资源建立：父 id 从 args 取（作 service 的 parent_param），其余字段作 data。"""

    async def handler(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
        return await getattr(plan_service, method)(
            db,
            owner=_require_owner_hasn_id(ctx),
            **{parent_param: int(args[parent_key])},
            data=_without(args, parent_key),
        )

    return handler


# ── 特例 handler ──────────────────────────────────────────────────────────────
async def _h_capture(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """零摩擦捕获：落收件箱（本地注入 status=inbox, source=chat）。PE-7 空间归属由 scope 解析。"""
    ws = await _resolve_write_scope(db, ctx, args)
    if not ws.ok:
        return _reject_not_in_enterprise()
    data = {**_without(args, 'scope', '_active_enterprise_id'), 'status': 'inbox', 'source': 'chat'}
    return await plan_service.create_todo(
        db, owner=_require_owner_hasn_id(ctx), data=data, enterprise_id=ws.enterprise_id, dept_id=ws.dept_id
    )


async def _h_triage(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await plan_service.update_todo(db, owner=_require_owner_hasn_id(ctx), pk=int(args['id']), data=_without(args, 'id'))


async def _h_today(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    day_start = _parse_dt(args['start'])
    day_end = _parse_dt(args['end'])
    if day_start is None or day_end is None:
        raise ValueError('今日概览必须提供完整的 start 和 end 时间范围')
    return await plan_service.today_overview(
        db, owner=_require_owner_hasn_id(ctx), day_start=day_start, day_end=day_end
    )


async def _h_list_events(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    return await plan_service.list_events(
        db, owner=_require_owner_hasn_id(ctx), start=_parse_dt(args.get('start')), end=_parse_dt(args.get('end'))
    )


async def _register_plan_artifact(
    db: Any, ctx: AgentContext, *, ref_type: str, result: Any
) -> ArtifactRegistration | None:
    """register-on-write（doc31/32 RC-P8「直建」半场）：分身**直建**目标/计划（多在主会话对话里
    捕获、无工作会话）时，在 create 处即把该资源登记进 `hasn_artifacts`——不必等工作会话完成投影
    （直建根本没有会话可投影），资源当场进「分身产物 tab / 会话资源栏」可点开。

    治「分身在主会话替我建了目标/计划，产物 tab / 资源栏却看不到、点不开」：plan 是**多资源**应用，
    据 `ref_type`（goal/plan）解析对应 descriptor + `hasn://plan/{goals|plans}/{id}` 深链。id 即
    云端权威 plan id（Core-08 第二原则：plan 的 goal/plan id 本就是云端权威 id，直接用、不反查本地）。

    - `session_id = ctx.session_id`：主会话直建为 None（产物仍凭 resource_uri 进产物 tab）；
      恰在工作会话内直建则戳会话、与完成投影同键幂等（两路径重复触发也只一条 active 行）。
    - best-effort：登记失败**绝不**拖垮 plan 写本身（同一事务，抛出会连累 goal/plan 落库）。

    doc36 U1：改走**公共接缝** `register_app_resource_artifact`（此前绕过接缝自己 resolve descriptor +
    直调 service，故 `ROLLOUT` 守卫覆盖不到 plan）。plan 的 `ref_type`（goal/plan）与 `resource_kind`
    （`plan.goal`/`plan.plan`）一一对应，直接拼 `plan.{ref_type}` 即可，不必再走 resolve。
    返回 `ArtifactRegistration` 供写工具把 `uri` 放进返回体。
    """
    server_id = str((result or {}).get('id') or '').strip()
    if not server_id:
        return None
    title = str((result or {}).get('title') or '').strip() or _PLAN_REF_TYPE_LABELS.get(ref_type, '计划')
    return await register_app_resource_artifact(
        db,
        app_id='plan',
        resource_kind=f'plan.{ref_type}',
        server_id=server_id,
        session_id=ctx.session_id,
        agent_hasn_id=ctx.agent_hasn_id,
        owner_hasn_id=_require_owner_hasn_id(ctx),
        title=title,
        source_tool=f'{NAMESPACE}.write',
    )


async def _h_create_goal(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """建目标：service 建行后 register-on-write 直登记 hasn_artifacts（doc31 RC-P8 直建半场）。"""
    result = await plan_service.create_goal(db, owner=_require_owner_hasn_id(ctx), data=args)
    registration = await _register_plan_artifact(db, ctx, ref_type='goal', result=result)
    return merge_resource_uri(result, registration)


async def _h_create_plan(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """建计划：缺省把计划绑定「调用方分身自己」（身份取自凭证）；PE-7 空间归属由 scope 解析。

    建行后 register-on-write 直登记 hasn_artifacts（doc31 RC-P8 直建半场，见 [`_register_plan_artifact`]）。
    """
    ws = await _resolve_write_scope(db, ctx, args)
    if not ws.ok:
        return _reject_not_in_enterprise()
    result = await plan_service.create_plan(
        db,
        owner=_require_owner_hasn_id(ctx),
        data=_without(args, 'scope', '_active_enterprise_id'),
        default_bound_agent=ctx.agent_hasn_id,
        enterprise_id=ws.enterprise_id,
        dept_id=ws.dept_id,
    )
    registration = await _register_plan_artifact(db, ctx, ref_type='plan', result=result)
    return merge_resource_uri(result, registration)


async def _h_create_todo(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """建待办：actor=agent/collab 的委托待办必填 notes（P6-A）；PE-7 空间归属由 scope 解析。"""
    actor = str(args.get('actor') or '').strip()
    if actor in ('agent', 'collab') and not str(args.get('notes') or '').strip():
        return {
            'ok': False,
            'error': 'notes_required',
            'message': 'actor=agent/collab 的委托待办必须填写 notes（详细任务描述：怎么做、验收标准）',
        }
    ws = await _resolve_write_scope(db, ctx, args)
    if not ws.ok:
        return _reject_not_in_enterprise()
    return await plan_service.create_todo(
        db,
        owner=_require_owner_hasn_id(ctx),
        data=_without(args, 'scope', '_active_enterprise_id'),
        enterprise_id=ws.enterprise_id,
        dept_id=ws.dept_id,
    )


async def _h_create_event(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """建日程：PE-7 空间归属由 scope 解析；企业事件自动展开组织者行 + 受邀参会行（[04] §6.3）。"""
    ws = await _resolve_write_scope(db, ctx, args)
    if not ws.ok:
        return _reject_not_in_enterprise()
    attendees = _as_list(args.get('attendees')) if ws.enterprise_id is not None else None
    return await plan_service.create_event(
        db,
        owner=_require_owner_hasn_id(ctx),
        data=_without(args, 'scope', '_active_enterprise_id', 'attendees'),
        enterprise_id=ws.enterprise_id,
        dept_id=ws.dept_id,
        attendees=attendees,
    )


async def _h_event_invite(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """加/减参会人（组织者=事件 owner）：写 event_attendee + 给新增者发邀请卡（[04] §6.2/§6.3）。

    邀请卡通知走共享 `notify_invited`（`plan_notify`）——与主人 WebUI 路径共用同一实现。
    """
    result = await plan_service.invite_attendees(
        db,
        owner=_require_owner_hasn_id(ctx),
        event_id=int(args['event_id']),
        add=_as_list(args.get('add')),
        remove=_as_list(args.get('remove')),
    )
    await notify_invited(
        db, event_id=int(args['event_id']), added=result.get('added') or [], organizer_name=ctx.agent_name or '组织者'
    )
    return result


async def _h_event_rsvp(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """代主人回复参会 RSVP（accepted/declined/tentative）。"""
    return await plan_service.respond_rsvp(
        db, owner=_require_owner_hasn_id(ctx), event_id=int(args['event_id']), rsvp=str(args['rsvp'])
    )


async def _h_availability(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """查企业成员忙闲找空档（只回忙闲块、不回标题；受 A3 数据范围约束）。需 enterprise_id + members。"""
    return await plan_service.member_availability(
        db,
        viewer_owner_hasn_id=_require_owner_hasn_id(ctx),
        enterprise_id=int(args['enterprise_id']),
        member_hasn_ids=_as_list(args.get('members')),
        start=_parse_dt(args.get('start')),
        end=_parse_dt(args.get('end')),
    )


async def _h_checkin(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """习惯打卡：把入参 date 归一到 service 的 checkin_date（缺省今日由 service 兜底）。"""
    data = _without(args, 'id')
    if 'date' in data and 'checkin_date' not in data:
        data['checkin_date'] = data.pop('date')
    return await plan_service.checkin_habit(db, owner=_require_owner_hasn_id(ctx), habit_id=int(args['id']), data=data)


async def _h_update_milestone(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """改里程碑（含改状态 planned→doing→done）：service 签名用 milestone_id（异构于 pk 类），故走特例 handler。"""
    return await plan_service.update_milestone(
        db, owner=_require_owner_hasn_id(ctx), milestone_id=int(args['id']), data=_without(args, 'id')
    )


async def _h_delete_milestone(db: Any, ctx: AgentContext, args: dict[str, Any]) -> Any:
    """删里程碑（其下待办 SET NULL 回落）：service 签名用 milestone_id，走特例 handler。"""
    await plan_service.delete_milestone(db, owner=_require_owner_hasn_id(ctx), milestone_id=int(args['id']))
    return {'deleted': True}


# ── schema 构造小工具 ──────────────────────────────────────────────────────────
def _s(desc: str) -> dict[str, Any]:
    return {'type': 'string', 'description': desc}


def _i(desc: str) -> dict[str, Any]:
    return {'type': 'integer', 'description': desc}


def _arr(desc: str) -> dict[str, Any]:
    return {'type': 'array', 'description': desc}


def _o(desc: str) -> dict[str, Any]:
    return {'type': 'object', 'description': desc}


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {'type': 'object', 'properties': props}
    if required:
        out['required'] = required
    return out


def _scope_prop() -> dict[str, Any]:
    """PE-7 空间入参：写类工具唯一空间维度（默认 personal；enterprise 从活跃企业空间解析归属）。"""
    return _s(
        '可选：空间 personal（个人，默认）| enterprise（活跃企业空间）；enterprise 但不在企业空间时拒绝，不自动切换'
    )


# ── 工具规格（action → name=hasn.plan.<action>；1:1 镜像原 hasn-mcp plan.rs）──────────
_SPECS: list[dict[str, Any]] = [
    # —— 复合一级（PURE_RELAY 部分）——
    {
        'action': 'capture',
        'write': True,
        'handler': _h_capture,
        'desc': '零摩擦捕获：把主人一句话落入收件箱（status=inbox）。分身先读懂自然语言再填结构化字段。',
        'schema': _schema(
            {
                'title': _s('一句话标题（必填）'),
                'actor': _s(
                    '可选：owner(亲为)|owner_decision(待你决策)|collab(协作)|agent(分身自主)（分诊后填，判不准留空）'
                ),
                'due_at': _s('可选：截止/发生时间（RFC3339）'),
                'estimated_minutes': _i('可选：预估分钟'),
                'energy': _s('可选：精力档 high|medium|low'),
                'priority': _s('可选：优先级'),
                'context_tags': _arr('可选：情境标签'),
                'note': _s('可选：备注/原话'),
                'scope': _scope_prop(),
            },
            ['title'],
        ),
    },
    {
        'action': 'triage',
        'write': True,
        'handler': _h_triage,
        'desc': '把收件箱条目分诊归类：传 id + 决定字段（actor/status/plan_id/goal_id 等），忠实持久化分身判定。',
        'schema': _schema(
            {
                'id': _i('收件箱待办 id（必填）'),
                'actor': _s('归属：owner(亲为·线下)|owner_decision(待你决策·可派发)|collab(待你确认)|agent(分身自主)'),
                'status': _s('目标状态：todo|scheduled'),
                'plan_id': _i('可选：挂到计划'),
                'goal_id': _i('可选：挂到目标'),
                'due_at': _s('可选：截止时间（RFC3339）'),
                'estimated_minutes': _i('可选：预估分钟'),
                'priority': _s('可选：优先级'),
            },
            ['id'],
        ),
    },
    {
        'action': 'today',
        'write': False,
        'handler': _h_today,
        'desc': '今日首屏聚合（时间轴 events + 待办池），必传 start/end（RFC3339 当日边界）。确定性读。',
        'schema': _schema(
            {'start': _s('当日起始（RFC3339，必填）'), 'end': _s('当日结束（RFC3339，必填）')},
            ['start', 'end'],
        ),
    },
    # —— goal ——
    {
        'action': 'goal.list',
        'write': False,
        'handler': _h_list('list_goals', 'status'),
        'desc': '列目标，可选按 status 过滤。确定性读。',
        'schema': _schema({'status': _s('可选：按状态过滤')}),
    },
    {
        'action': 'goal.get',
        'write': False,
        'handler': _h_get('get_goal'),
        'desc': '取目标详情（含关键结果 KR）。确定性读。',
        'schema': _schema({'id': _i('目标 id（必填）')}, ['id']),
    },
    {
        'action': 'goal.create',
        'write': True,
        'handler': _h_create_goal,
        'desc': '建目标：title 必填；可选 description/category/target_date/status/priority。',
        'schema': _schema(
            {
                'title': _s('目标标题（必填）'),
                'description': _s('可选：描述'),
                'category': _s('可选：品类'),
                'target_date': _s('可选：目标日期'),
                'priority': _s('可选：优先级'),
            },
            ['title'],
        ),
    },
    {
        'action': 'goal.update',
        'write': True,
        'handler': _h_update('update_goal'),
        'desc': '改目标：传 id + 要改的字段。',
        'schema': _schema(
            {
                'id': _i('目标 id（必填）'),
                'title': _s('可选：标题'),
                'description': _s('可选：描述'),
                'status': _s('可选：状态'),
                'progress': _i('可选：进度 0-100'),
            },
            ['id'],
        ),
    },
    {
        'action': 'goal.delete',
        'write': True,
        'handler': _h_delete('delete_goal'),
        'desc': '删目标（其下计划/待办 SET NULL 回落，不级联丢失）。传 id。',
        'schema': _schema({'id': _i('目标 id（必填）')}, ['id']),
    },
    {
        'action': 'goal.add_key_result',
        'write': True,
        'handler': _h_create_child('create_kr', 'goal_id', 'goal_id'),
        'desc': '给目标加关键结果（KR）。云端字段是 metric（不是 title）。',
        'schema': _schema(
            {
                'goal_id': _i('目标 id（必填）'),
                'metric': _s('指标名（必填，如「体重」「完成章节数」——它就是 KR 的显示名）'),
                'target_value': _s('目标值（必填，可量化的目标数，如 80 / 12 / 1000）'),
                'unit': _s('可选：单位（如 kg / 章 / 个 / %）'),
                'current_value': _s('可选：当前值（默认 0；用于度量进度）'),
                'direction': _s('可选：方向 up（越高越好，默认）| down（越低越好，如减重/降本）'),
            },
            ['goal_id', 'metric', 'target_value'],
        ),
    },
    # —— project（云端 plan 实体）——
    {
        'action': 'project.list',
        'write': False,
        'handler': _h_list('list_plans', 'status', 'goal_id'),
        'desc': '列计划，可选按 status / goal_id 过滤。确定性读。',
        'schema': _schema({'status': _s('可选：按状态过滤'), 'goal_id': _i('可选：按所属目标过滤')}),
    },
    {
        'action': 'project.get',
        'write': False,
        'handler': _h_get('get_plan'),
        'desc': '取计划详情（含里程碑）。确定性读。',
        'schema': _schema({'id': _i('计划 id（必填）')}, ['id']),
    },
    {
        'action': 'project.create',
        'write': True,
        'handler': _h_create_plan,
        'desc': '建计划：title 必填；可选 goal_id/description/status/start_date/due_date/bound_agent_id。',
        'schema': _schema(
            {
                'title': _s('计划标题（必填）'),
                'goal_id': _i('可选：所属目标（强烈建议挂到目标，别建孤立计划）'),
                'description': _s('可选：描述'),
                'status': _s('可选：状态'),
                'start_date': _s('可选：开始日期'),
                'due_date': _s('可选：截止日期'),
                'bound_agent_id': _s('可选：协作分身 hasn_id。留空即自动绑定你自己；要交给别的分身才显式传'),
                'scope': _scope_prop(),
            },
            ['title'],
        ),
    },
    {
        'action': 'project.update',
        'write': True,
        'handler': _h_update('update_plan'),
        'desc': '改计划：传 id + 要改的字段。',
        'schema': _schema(
            {
                'id': _i('计划 id（必填）'),
                'title': _s('可选：标题'),
                'status': _s('可选：状态'),
                'due_date': _s('可选：截止日期'),
            },
            ['id'],
        ),
    },
    {
        'action': 'project.delete',
        'write': True,
        'handler': _h_delete('delete_plan'),
        'desc': '删计划（其下待办 SET NULL 回落收件箱）。传 id。',
        'schema': _schema({'id': _i('计划 id（必填）')}, ['id']),
    },
    {
        'action': 'project.add_milestone',
        'write': True,
        'handler': _h_create_child('create_milestone', 'plan_id', 'plan_id'),
        'desc': '给计划加里程碑：传 plan_id + title/due_date 等。',
        'schema': _schema(
            {
                'plan_id': _i('计划 id（必填）'),
                'title': _s('里程碑标题（必填）'),
                'due_date': _s('可选：到期日'),
                'status': _s('可选：状态 planned(未开始)|doing(进行中)|done(已完成)，默认 planned'),
            },
            ['plan_id', 'title'],
        ),
    },
    {
        'action': 'milestone.update',
        'write': True,
        'handler': _h_update_milestone,
        'desc': (
            '改里程碑（传 id + 要改的字段）——**推进这个阶段时用它推动里程碑前进/收尾**：'
            '开工把 `status` 置 `doing`、本阶段待办全做完/交付了就置 `done`（done↔完成双向派生）；'
            '也可改 title/due_date。'
        ),
        'schema': _schema(
            {
                'id': _i('里程碑 id（必填）'),
                'title': _s('可选：标题'),
                'status': _s('可选：状态 planned(未开始)|doing(进行中)|done(已完成)'),
                'due_date': _s('可选：到期日'),
            },
            ['id'],
        ),
    },
    {
        'action': 'milestone.delete',
        'write': True,
        'handler': _h_delete_milestone,
        'desc': '删里程碑（其下待办 SET NULL 回落，不级联丢失）。传 id。',
        'schema': _schema({'id': _i('里程碑 id（必填）')}, ['id']),
    },
    # —— todo ——
    {
        'action': 'todo.list',
        'write': False,
        'handler': _h_list('list_todos', 'status', 'actor', 'plan_id', 'goal_id'),
        'desc': '列待办，可选按 status/actor/plan_id/goal_id 过滤。确定性读。',
        'schema': _schema({
            'status': _s('可选：按状态过滤（inbox/todo/scheduled/doing/...）'),
            'actor': _s('可选：按归属过滤 owner|collab|agent'),
            'plan_id': _i('可选：按所属计划过滤'),
            'goal_id': _i('可选：按所属目标过滤'),
        }),
    },
    {
        'action': 'todo.get',
        'write': False,
        'handler': _h_get('get_todo'),
        'desc': '取待办详情。确定性读。',
        'schema': _schema({'id': _i('待办 id（必填）')}, ['id']),
    },
    {
        'action': 'todo.create',
        'write': True,
        'handler': _h_create_todo,
        'desc': '建待办。actor=agent/collab 的委托待办必填 notes。',
        'schema': _schema(
            {
                'title': _s('待办标题（必填）'),
                'notes': _s('详细任务描述（怎么做）。actor=agent/collab 的委托待办必填'),
                'output_spec': _o(
                    '输出要求（编排时定「做出什么」）：{required:bool, label?, expects:[{...}]}。'
                    'expects 每条二选一：应用资源写 resource_kind（如 knowledge.base / deck.presentation），'
                    '非应用资源写 artifact_kind∈document|image|video|voice|file。'
                    '多条之间是「或」（满足任一即过）；写错或两个都填会直接报错，不会静默放行。'
                ),
                'actor': _s('可选：归属 owner(亲为)|owner_decision(待你决策)|collab(协作)|agent(分身自主)'),
                'status': _s('可选：状态（默认 todo）'),
                'priority': _s('可选：优先级'),
                'due_at': _s('可选：截止时间（RFC3339）'),
                'estimated_minutes': _i('可选：预估分钟'),
                'energy': _s('可选：精力档'),
                'plan_id': _i('可选：所属计划'),
                'goal_id': _i('可选：所属目标'),
                'milestone_id': _i('可选：所属里程碑（须与 plan_id 同计划；未定 plan_id 则随里程碑落其计划）'),
                'context_tags': _arr('可选：情境标签'),
                'scope': _scope_prop(),
            },
            ['title'],
        ),
    },
    {
        'action': 'todo.update',
        'write': True,
        'handler': _h_update('update_todo'),
        'desc': (
            '改待办（含改归属/状态/排期字段）：传 id + 要改的字段。'
            '状态按八态机流转（服务端白名单校验，非法跳转拒 invalid_status_transition）；'
            '置 done 走 P6-C 完成闸：output_spec.required 且无匹配产物 → 拒 output_not_satisfied，'
            '需先经生产型工具产出产物（自动落 hasn_artifacts，origin_ref 会话已透传），'
            '或停 waiting_review 诚实回报缺口。'
            '完成必带 completion_note 说明交付；放弃写 cancel_reason。'
        ),
        'schema': _schema(
            {
                'id': _i('待办 id（必填）'),
                'title': _s('可选：标题'),
                'actor': _s('可选：归属 owner(亲为)|owner_decision(待你决策)|collab(协作)|agent(分身自主)'),
                'status': _s('可选：状态 inbox|todo|scheduled|doing|waiting_review|done|cancelled（八态机流转）'),
                'priority': _s('可选：优先级'),
                'due_at': _s('可选：截止时间（RFC3339）'),
                'milestone_id': _i('可选：所属里程碑（须与 plan_id 同计划）'),
                'decision_note': _s('可选：owner_decision 决策留痕（备好的背景/选项）'),
                'completion_note': _s('可选：完成结论（置 done 建议必带）'),
                'cancel_reason': _s('可选：放弃原因（置 cancelled 时写）'),
            },
            ['id'],
        ),
    },
    {
        'action': 'todo.delete',
        'write': True,
        'handler': _h_delete('delete_todo'),
        'desc': '删待办。传 id。',
        'schema': _schema({'id': _i('待办 id（必填）')}, ['id']),
    },
    # —— event ——
    {
        'action': 'event.list',
        'write': False,
        'handler': _h_list_events,
        'desc': '列日程/时间块（区间，传 start/end RFC3339）。确定性读。',
        'schema': _schema({'start': _s('可选：区间起（RFC3339）'), 'end': _s('可选：区间止（RFC3339）')}),
    },
    {
        'action': 'event.create',
        'write': True,
        'handler': _h_create_event,
        'desc': (
            '建日程/时间块。actor∈owner/collab/attend（分身自主不占日历）；可挂 todo_id（flex 块）。'
            'scope=enterprise 建企业会议：自动落组织者行 + 按 attendees 展开受邀参会人（被邀即上其日历）。'
        ),
        'schema': _schema(
            {
                'title': _s('日程标题（必填）'),
                'start_at': _s('开始（RFC3339，必填）'),
                'end_at': _s('结束（RFC3339，必填）'),
                'actor': _s('可选：owner|collab|attend（分身自主不占日历）'),
                'kind': _s('可选：fixed(固定)|flex(弹性)|break(休息)'),
                'todo_id': _i('可选：关联待办（flex 时间块）'),
                'locked': _s('可选：是否锁定（true/false）'),
                'visibility': _s('可选（仅企业事件）：private(仅参与者+被授权，默认)|public(企业公开)'),
                'attendees': _arr('可选（仅企业事件）：受邀参会人 hasn_id 列表；组织者=你自己会自动加入，无需列出'),
                'source': _s('可选：来源 manual|chat|capture|decompose|oa_meeting|oa_interview（OA 注入时传）'),
                'origin_ref': _s(
                    '可选（OA 注入）：外部来源锚 oa:room_booking:{id} / oa:interview:{id}，供 OA 回写反查'
                ),
                'scope': _scope_prop(),
            },
            ['title', 'start_at', 'end_at'],
        ),
    },
    {
        'action': 'event.update',
        'write': True,
        'handler': _h_update('update_event'),
        'desc': '改日程（拖拽改期即改 start_at/end_at）：传 id + 要改的字段。',
        'schema': _schema(
            {
                'id': _i('日程 id（必填）'),
                'start_at': _s('可选：开始（RFC3339）'),
                'end_at': _s('可选：结束（RFC3339）'),
                'title': _s('可选：标题'),
                'locked': _s('可选：是否锁定'),
            },
            ['id'],
        ),
    },
    {
        'action': 'event.delete',
        'write': True,
        'handler': _h_delete('delete_event'),
        'desc': '删日程。传 id。',
        'schema': _schema({'id': _i('日程 id（必填）')}, ['id']),
    },
    # —— event 参会协同（企业会议，plan:manage / plan:read）——
    {
        'action': 'event.invite',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_event_invite,
        'desc': (
            '企业会议加/减参会人（仅你组织的企业事件可调）：add 新增、remove 移除；'
            '新增者会收到会议邀请卡（深链会议详情）且立即上其日历。组织者本人不可移除。'
        ),
        'schema': _schema(
            {
                'event_id': _i('企业会议事件 id（必填，须是你组织的）'),
                'add': _arr('可选：新增参会人 hasn_id 列表'),
                'remove': _arr('可选：移除参会人 hasn_id 列表'),
            },
            ['event_id'],
        ),
    },
    {
        'action': 'event.rsvp',
        'write': True,
        'scopes': [SCOPE_MANAGE],
        'handler': _h_event_rsvp,
        'desc': '代主人回复会议 RSVP（accepted 接受 / declined 拒绝 / tentative 待定）。传 event_id + rsvp。',
        'schema': _schema(
            {
                'event_id': _i('会议事件 id（必填，你须是其参会人）'),
                'rsvp': _s('回执：accepted | declined | tentative（必填）'),
            },
            ['event_id', 'rsvp'],
        ),
    },
    {
        'action': 'availability',
        'write': False,
        'scopes': [SCOPE_READ],
        'handler': _h_availability,
        'desc': (
            '查企业成员忙闲找空档（受你的数据范围可见性约束，只回忙闲块、不回标题/内容）。'
            '传 enterprise_id + members（成员 hasn_id 列表）+ 可选 start/end 时间窗。'
        ),
        'schema': _schema(
            {
                'enterprise_id': _i('企业 id（必填）'),
                'members': _arr('要查忙闲的成员 hasn_id 列表（必填）；超你数据范围的成员会被自动过滤掉'),
                'start': _s('可选：时间窗起（RFC3339）'),
                'end': _s('可选：时间窗止（RFC3339）'),
            },
            ['enterprise_id', 'members'],
        ),
    },
    # —— habit ——
    {
        'action': 'habit.list',
        'write': False,
        'handler': _h_list('list_habits', 'status'),
        'desc': '列习惯，可选按 status 过滤。确定性读。',
        'schema': _schema({'status': _s('可选：按状态过滤')}),
    },
    {
        'action': 'habit.create',
        'write': True,
        'handler': _h_create('create_habit'),
        'desc': '建习惯：title 必填；可选 cadence/target_per_period/goal_id。',
        'schema': _schema(
            {
                'title': _s('习惯标题（必填）'),
                'cadence': _s('可选：频率 daily|weekly|...'),
                'target_per_period': _i('可选：周期内目标次数'),
                'goal_id': _i('可选：关联目标'),
            },
            ['title'],
        ),
    },
    {
        'action': 'habit.checkin',
        'write': True,
        'handler': _h_checkin,
        'desc': '习惯打卡：传 id + 可选 date/note。',
        'schema': _schema(
            {
                'id': _i('习惯 id（必填）'),
                'date': _s('可选：打卡日期（默认今日）'),
                'note': _s('可选：备注'),
            },
            ['id'],
        ),
    },
    # —— preference ——
    {
        'action': 'preference.get',
        'write': False,
        'handler': _h_list('get_preference'),
        'desc': '取排程偏好（working_hours/energy/buffer/no_schedule 等）。确定性读。',
        'schema': _schema({}),
    },
    {
        'action': 'preference.set',
        'write': True,
        'handler': _h_create('upsert_preference'),
        'desc': '改排程偏好（upsert）：传要改的偏好字段。',
        'schema': _schema({
            'working_hours': _s('可选：工作时段 JSON'),
            'energy_curve': _s('可选：精力曲线 JSON'),
            'buffer_minutes': _i('可选：缓冲分钟'),
            'no_schedule': _s('可选：勿排时段 JSON'),
        }),
    },
]


# ── L3 工具门档位（doc08 §4·RT3·云端半场）────────────────────────────────────────
# 对外会话里按对端信任档硬门控（主会话/主人自环不受限，见 server.call_tool + trust_gate）。
# 只给「披露主人日程/计划/偏好」的读工具 + 「代主人预约」的写工具点名挂门，其余（建自己的
# 目标/待办、分诊、习惯打卡等主人自身操作）**不挂门**（对外会话本就不涉及披露/代主人对外承诺）。
#   看日程/看计划/看待办/看项目/看习惯/忙闲/偏好读 = 好友(3)
#   代预约（event.create=在主人日历上落一场约会 ≈ make_appointment）= 密友(4)
_MIN_TRUST_BY_ACTION: dict[str, int] = {
    # —— 看日程/看计划（读，档=好友3）——
    'today': 3,
    'goal.list': 3,
    'goal.get': 3,
    'project.list': 3,
    'project.get': 3,
    'todo.list': 3,
    'todo.get': 3,
    'event.list': 3,
    'availability': 3,
    'habit.list': 3,
    # —— 偏好详情（读，档=好友3）——
    'preference.get': 3,
    # —— 代预约（写，档=密友4；make_appointment 语义）——
    'event.create': 4,
}


class _PlanTool(BaseTool):
    """plan 域单 struct + spec 派发（避免 29 个同形态类样板，对齐 Rust PlanOp 枚举派发）。"""

    def __init__(self, spec: dict[str, Any]) -> None:
        self._action = spec['action']
        self._name = f'{NAMESPACE}.{spec["action"]}'
        self._desc = spec['desc']
        self._input_schema = spec['schema']
        self._write = bool(spec['write'])
        self._handler: Handler = spec['handler']
        # 显式 scope 覆盖（invite/rsvp=plan:manage、availability=plan:read）；缺省按 write 回落 plan:write。
        self._scopes: list[str] | None = spec.get('scopes')

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
    def min_trust_level(self) -> int | None:
        # L3 工具门（doc08 §4·RT3·云端半场）：披露主人日程/计划/偏好读 = 好友(3)、代预约写 = 密友(4)；
        # 未点名的 action（建目标/分诊/习惯打卡等主人自身操作）无对外门（None）。
        return _MIN_TRUST_BY_ACTION.get(self._action)

    @property
    def description(self) -> str:
        return self._desc

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._input_schema

    @property
    def required_scopes(self) -> list[str]:
        # 显式 scopes 优先（invite/rsvp=plan:manage、availability=plan:read）；否则写类=plan:write、读类无 scope。
        if self._scopes is not None:
            return list(self._scopes)
        return [SCOPE_WRITE] if self._write else []

    async def execute(self, agent_context: AgentContext, arguments: dict[str, Any]) -> Any:
        # 维度① 三态由 server.call_tool 统一判定（D3），工具内不二次校验。
        if self._write:
            # service 写方法只 flush 不 commit → 用 .begin() 自动提交；best-effort 发 WSPUSH 失效。
            async with async_db_session.begin() as db:
                result = await self._handler(db, agent_context, arguments)
                await _safe_bump(db, agent_context.owner_hasn_id)
                return result
        async with async_db_session() as db:
            return await self._handler(db, agent_context, arguments)


PLAN_TOOLS: list[_PlanTool] = [_PlanTool(spec) for spec in _SPECS]

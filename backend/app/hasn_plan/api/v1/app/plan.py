"""规划与目标管理 Owner/WebUI 端 API（P1）。

路由前缀: /api/v1/plan/app
认证方式: Owner JWT（owner hasn_id 由登录用户解析，绝不读请求体身份）。

定位：daemon `domains/plan` 的 webui-facing 读写通道——hasn-node daemon 以 Owner JWT
（`BackendGateway::owner_transport`）回源这些端点，本地 SQLite 镜像做 local_first，WebUI 只调 daemon。
与 agent 端（`/api/v1/plan/agent/*`）共用同一 `plan_service`，仅身份来源不同。
"""

import logging

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Path, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_core import hasn_humans_dao
from backend.app.hasn_plan.service.plan_app_service import plan_service
from backend.app.hasn_plan.service.plan_authz import active_enterprise_id
from backend.app.hasn_plan.service.plan_notify import notify_invited
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()
log = logging.getLogger(__name__)


async def _resolve_owner_human(db: AsyncSession, request: Request) -> Any:
    """登录用户 → HASN 主人身份行（`HasnHumans`）。"""
    human = await hasn_humans_dao.get_by_user_id(db, request.user.id)
    if not human:
        raise errors.NotFoundError(msg='用户 HASN 身份不存在')
    return human


async def _resolve_owner(db: AsyncSession, request: Request) -> str:
    """登录用户 → HASN 主人 hasn_id。"""
    return (await _resolve_owner_human(db, request)).hasn_id


async def _bump_plan_sync(db: AsyncSession, owner_hasn_id: str) -> None:
    """owner 写点后 → WSPUSH ``hasn.sync.invalidate(plan)`` 给该 owner 在线节点（best-effort）。

    plan 是 owner 定向 kind（PLAN-P2，对齐 tasks）：走 ``bump_owner`` 仅推该 owner 的在线节点，
    revision 是 per-owner 维度（不进全局握手）。push 失败不抛——离线设备靠重连握手 + 周期对账追平。
    """
    try:
        from backend.app.hasn.service import sync_invalidate_service as siv

        await siv.bump_owner(siv.KIND_PLAN, db, owner_hasn_id)
    except Exception as e:  # 推送 best-effort
        log.warning('[plan] sync invalidate 推送失败 (非致命): %s', e)


# ── goal ──────────────────────────────────────────────────────────────────────
@router.get('/goals', summary='我的目标列表', dependencies=[DependsJwtAuth])
async def app_list_goals(
    request: Request, db: CurrentSession, status: Annotated[str | None, Query()] = None
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    return response_base.success(data=await plan_service.list_goals(db, owner=owner, status=status))


@router.post('/goals', summary='创建目标', dependencies=[DependsJwtAuth])
async def app_create_goal(
    request: Request, db: CurrentSessionTransaction, body: Annotated[dict[str, Any], Body()]
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.create_goal(db, owner=owner, data=body)
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.get('/goals/{pk}', summary='目标详情（含 KR）', dependencies=[DependsJwtAuth])
async def app_get_goal(request: Request, db: CurrentSession, pk: Annotated[int, Path(ge=1)]) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    return response_base.success(data=await plan_service.get_goal(db, owner=owner, pk=pk))


@router.put('/goals/{pk}', summary='更新目标', dependencies=[DependsJwtAuth])
async def app_update_goal(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.update_goal(db, owner=owner, pk=pk, data=body)
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.delete('/goals/{pk}', summary='删除目标', dependencies=[DependsJwtAuth])
async def app_delete_goal(
    request: Request, db: CurrentSessionTransaction, pk: Annotated[int, Path(ge=1)]
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    await plan_service.delete_goal(db, owner=owner, pk=pk)
    await _bump_plan_sync(db, owner)
    return response_base.success(data={'deleted': True})


@router.post('/goals/{pk}/recompute-progress', summary='重算目标进度（派生）', dependencies=[DependsJwtAuth])
async def app_recompute_goal(
    request: Request, db: CurrentSessionTransaction, pk: Annotated[int, Path(ge=1)]
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    return response_base.success(data=await plan_service.recompute_goal_progress(db, owner=owner, pk=pk))


@router.post('/goals/{pk}/key-results', summary='添加 KR', dependencies=[DependsJwtAuth])
async def app_create_kr(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.create_kr(db, owner=owner, goal_id=pk, data=body)
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.put('/key-results/{kr_id}', summary='更新 KR', dependencies=[DependsJwtAuth])
async def app_update_kr(
    request: Request,
    db: CurrentSessionTransaction,
    kr_id: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.update_kr(db, owner=owner, kr_id=kr_id, data=body)
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.delete('/key-results/{kr_id}', summary='删除 KR', dependencies=[DependsJwtAuth])
async def app_delete_kr(
    request: Request, db: CurrentSessionTransaction, kr_id: Annotated[int, Path(ge=1)]
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    await plan_service.delete_kr(db, owner=owner, kr_id=kr_id)
    await _bump_plan_sync(db, owner)
    return response_base.success(data={'deleted': True})


# ── plan ──────────────────────────────────────────────────────────────────────
@router.get('/plans', summary='我的计划列表', dependencies=[DependsJwtAuth])
async def app_list_plans(
    request: Request,
    db: CurrentSession,
    status: Annotated[str | None, Query()] = None,
    goal_id: Annotated[int | None, Query()] = None,
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    return response_base.success(data=await plan_service.list_plans(db, owner=owner, status=status, goal_id=goal_id))


@router.post('/plans', summary='创建计划', dependencies=[DependsJwtAuth])
async def app_create_plan(
    request: Request, db: CurrentSessionTransaction, body: Annotated[dict[str, Any], Body()]
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.create_plan(db, owner=owner, data=body)
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.get('/plans/{pk}', summary='计划详情（含里程碑）', dependencies=[DependsJwtAuth])
async def app_get_plan(request: Request, db: CurrentSession, pk: Annotated[int, Path(ge=1)]) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    return response_base.success(data=await plan_service.get_plan(db, owner=owner, pk=pk))


@router.put('/plans/{pk}', summary='更新计划', dependencies=[DependsJwtAuth])
async def app_update_plan(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.update_plan(db, owner=owner, pk=pk, data=body)
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.delete('/plans/{pk}', summary='删除计划', dependencies=[DependsJwtAuth])
async def app_delete_plan(
    request: Request, db: CurrentSessionTransaction, pk: Annotated[int, Path(ge=1)]
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    await plan_service.delete_plan(db, owner=owner, pk=pk)
    await _bump_plan_sync(db, owner)
    return response_base.success(data={'deleted': True})


@router.post('/plans/{pk}/bound-agent', summary='设置/解绑计划协作分身（AppCollab）', dependencies=[DependsJwtAuth])
async def app_set_plan_bound_agent(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.set_plan_bound_agent(db, owner=owner, pk=pk, bound_agent_id=body.get('bound_agent_id'))
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.post('/plans/{pk}/milestones', summary='添加里程碑', dependencies=[DependsJwtAuth])
async def app_create_milestone(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.create_milestone(db, owner=owner, plan_id=pk, data=body)
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.put('/milestones/{milestone_id}', summary='更新里程碑', dependencies=[DependsJwtAuth])
async def app_update_milestone(
    request: Request,
    db: CurrentSessionTransaction,
    milestone_id: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.update_milestone(db, owner=owner, milestone_id=milestone_id, data=body)
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.delete('/milestones/{milestone_id}', summary='删除里程碑', dependencies=[DependsJwtAuth])
async def app_delete_milestone(
    request: Request, db: CurrentSessionTransaction, milestone_id: Annotated[int, Path(ge=1)]
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    await plan_service.delete_milestone(db, owner=owner, milestone_id=milestone_id)
    await _bump_plan_sync(db, owner)
    return response_base.success(data={'deleted': True})


# ── todo ──────────────────────────────────────────────────────────────────────
@router.get('/todos', summary='我的待办列表', dependencies=[DependsJwtAuth])
async def app_list_todos(
    request: Request,
    db: CurrentSession,
    status: Annotated[str | None, Query()] = None,
    actor: Annotated[str | None, Query()] = None,
    plan_id: Annotated[int | None, Query()] = None,
    goal_id: Annotated[int | None, Query()] = None,
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    return response_base.success(
        data=await plan_service.list_todos(
            db, owner=owner, status=status, actor=actor, plan_id=plan_id, goal_id=goal_id
        )
    )


@router.post('/todos', summary='创建待办', dependencies=[DependsJwtAuth])
async def app_create_todo(
    request: Request, db: CurrentSessionTransaction, body: Annotated[dict[str, Any], Body()]
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.create_todo(db, owner=owner, data=body)
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.get('/todos/{pk}', summary='待办详情', dependencies=[DependsJwtAuth])
async def app_get_todo(request: Request, db: CurrentSession, pk: Annotated[int, Path(ge=1)]) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    return response_base.success(data=await plan_service.get_todo(db, owner=owner, pk=pk))


@router.put('/todos/{pk}', summary='更新待办', dependencies=[DependsJwtAuth])
async def app_update_todo(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.update_todo(db, owner=owner, pk=pk, data=body)
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.delete('/todos/{pk}', summary='删除待办', dependencies=[DependsJwtAuth])
async def app_delete_todo(
    request: Request, db: CurrentSessionTransaction, pk: Annotated[int, Path(ge=1)]
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    await plan_service.delete_todo(db, owner=owner, pk=pk)
    await _bump_plan_sync(db, owner)
    return response_base.success(data={'deleted': True})


# ── event（日历）────────────────────────────────────────────────────────────────
@router.get('/events', summary='日程列表（区间）', dependencies=[DependsJwtAuth])
async def app_list_events(
    request: Request,
    db: CurrentSession,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    scope: Annotated[str, Query(description='personal（默认个人日历）| enterprise（活跃企业日历，WHO/WHAT 裁剪）')] = (
        'personal'
    ),
) -> ResponseModel:
    """日历事件读（PLAN-ENT B2 空间分叉，[04] §5.1）。

    `scope=personal`（默认）→ 个人事件（`enterprise_id IS NULL`，owner 隔离，个人零破坏）；
    `scope=enterprise` → 主人活跃企业 E 的事件（恒前置 `enterprise_id==E` + 数据范围 WHO + 忙闲 WHAT 裁剪）；
    无活跃企业 / 非成员 → 空列表（企业隔离硬底线）。两条各自 scope 读，webui 合并（不是一条混合查询）。
    """
    owner = await _resolve_owner(db, request)
    if (scope or 'personal').strip().lower() == 'enterprise':
        eid = await active_enterprise_id(db, owner)
        if not eid:
            return response_base.success(data=[])
        data = await plan_service.list_enterprise_events(
            db, viewer_owner_hasn_id=owner, enterprise_id=eid, start=start, end=end
        )
        return response_base.success(data=data)
    return response_base.success(data=await plan_service.list_events(db, owner=owner, start=start, end=end))


@router.post('/events', summary='创建日程/时间块', dependencies=[DependsJwtAuth])
async def app_create_event(
    request: Request, db: CurrentSessionTransaction, body: Annotated[dict[str, Any], Body()]
) -> ResponseModel:
    """创建日程/时间块（PLAN-ENT 显式 scope，[04] §5.3）。

    `scope=personal`（默认）→ 个人日程（`enterprise_id IS NULL`）；`scope=enterprise` → 主人活跃企业会议
    （服务端解析 `active_enterprise_id`，展开组织者行 + 按 `attendees` 展开受邀参会人，被邀即上其日历）。
    企业归属由**服务端**解析（不信任 body 里的 `enterprise_id`，CR 不变量 3）；无活跃企业 → 诚实拒绝，
    不静默落个人（与 agent 工具 `_h_create_event` 的 PE-7 解析一致，[04] §7）。
    """
    owner = await _resolve_owner(db, request)
    scope = str(body.get('scope') or 'personal').strip().lower()
    enterprise_id: int | None = None
    attendees: list[str] | None = None
    if scope == 'enterprise':
        enterprise_id = await active_enterprise_id(db, owner)
        if not enterprise_id:
            raise errors.RequestError(
                msg='当前无活跃企业空间，无法创建企业会议；请先切换到企业空间，或用个人日程（scope=personal）'
            )
        raw = body.get('attendees')
        attendees = [str(h).strip() for h in raw if str(h or '').strip()] if isinstance(raw, list) else []
    payload = {k: v for k, v in body.items() if k not in ('scope', 'attendees')}
    data = await plan_service.create_event(
        db, owner=owner, data=payload, enterprise_id=enterprise_id, attendees=attendees
    )
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.put('/events/{pk}', summary='更新日程', dependencies=[DependsJwtAuth])
async def app_update_event(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.update_event(db, owner=owner, pk=pk, data=body)
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.delete('/events/{pk}', summary='删除日程', dependencies=[DependsJwtAuth])
async def app_delete_event(
    request: Request, db: CurrentSessionTransaction, pk: Annotated[int, Path(ge=1)]
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    await plan_service.delete_event(db, owner=owner, pk=pk)
    await _bump_plan_sync(db, owner)
    return response_base.success(data={'deleted': True})


@router.post('/events/{pk}/reschedule', summary='拖动改期（自动锁定，§8.1）', dependencies=[DependsJwtAuth])
async def app_reschedule_event(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    start_at = datetime.fromisoformat(str(body['start_at']))
    end_at = datetime.fromisoformat(str(body['end_at']))
    data = await plan_service.reschedule_event(
        db, owner=owner, pk=pk, start_at=start_at, end_at=end_at, lock=bool(body.get('lock', True))
    )
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


# ── 企业会议协同（PLAN-ENT [04] §6.2，owner/WebUI 端；与 agent 工具共用同一 service）──────────
@router.get('/events/{pk}/attendees', summary='会议参会人名单（含 RSVP）', dependencies=[DependsJwtAuth])
async def app_list_attendees(
    request: Request, db: CurrentSession, pk: Annotated[int, Path(ge=1)]
) -> ResponseModel:
    """列出某企业会议的参会人（组织者 + 受邀人）及其 RSVP 状态（详情抽屉 RSVP 展示用）。"""
    await _resolve_owner(db, request)  # 仅校验登录主人身份；名单读不额外裁剪（事件本身受 A3 约束）
    return response_base.success(data=await plan_service.list_attendees(db, event_id=pk))


@router.post('/events/{pk}/invite', summary='加/减参会人（组织者）', dependencies=[DependsJwtAuth])
async def app_invite_attendees(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    """组织者（事件 owner）加/减参会人 + 给新增者发邀请卡（[04] §6.2/§6.3）。

    `body`: `{add?: [hasn_id...], remove?: [hasn_id...], default_role?}`。仅事件 owner 可调，
    组织者行不可移除。邀请卡通知走共享 `notify_invited`（与 agent 工具同一实现）。
    """
    human = await _resolve_owner_human(db, request)
    owner = human.hasn_id
    result = await plan_service.invite_attendees(
        db,
        owner=owner,
        event_id=pk,
        add=[str(h) for h in (body.get('add') or [])],
        remove=[str(h) for h in (body.get('remove') or [])],
        default_role=str(body.get('default_role') or 'required'),
    )
    await notify_invited(
        db, event_id=pk, added=result.get('added') or [], organizer_name=getattr(human, 'nickname', '') or '组织者'
    )
    await _bump_plan_sync(db, owner)
    return response_base.success(data=result)


@router.post('/events/{pk}/rsvp', summary='回复会议 RSVP', dependencies=[DependsJwtAuth])
async def app_respond_rsvp(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    """参会人（本人）回复 RSVP（accepted/declined/tentative）。仅本人参会行可改。"""
    owner = await _resolve_owner(db, request)
    data = await plan_service.respond_rsvp(db, owner=owner, event_id=pk, rsvp=str(body.get('rsvp') or ''))
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.get('/availability', summary='企业成员忙闲（找空档，只回忙闲不回标题）', dependencies=[DependsJwtAuth])
async def app_member_availability(
    request: Request,
    db: CurrentSession,
    members: Annotated[str, Query(description='逗号分隔的成员 hasn_id 列表（受 A3 可见性约束裁剪）')],
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> ResponseModel:
    """查活跃企业成员忙闲找空档（[04] §6.2，团队日历视图）。

    ⚠️ 性能约束（[05] §11 CR）：调用方**必须**带时间窗（`start`/`end`）+ 成员上限，禁无界全表扫。
    只回匿名忙碌块、不回标题（受 A3 数据范围约束，非成员/无活跃企业 → 空 dict）。
    """
    owner = await _resolve_owner(db, request)
    eid = await active_enterprise_id(db, owner)
    if not eid:
        return response_base.success(data={})
    member_ids = [m.strip() for m in (members or '').split(',') if m.strip()]
    data = await plan_service.member_availability(
        db, viewer_owner_hasn_id=owner, enterprise_id=eid, member_hasn_ids=member_ids, start=start, end=end
    )
    return response_base.success(data=data)


# ── habit ───────────────────────────────────────────────────────────────────────
@router.get('/habits', summary='习惯列表', dependencies=[DependsJwtAuth])
async def app_list_habits(
    request: Request, db: CurrentSession, status: Annotated[str | None, Query()] = None
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    return response_base.success(data=await plan_service.list_habits(db, owner=owner, status=status))


@router.post('/habits', summary='创建习惯', dependencies=[DependsJwtAuth])
async def app_create_habit(
    request: Request, db: CurrentSessionTransaction, body: Annotated[dict[str, Any], Body()]
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.create_habit(db, owner=owner, data=body)
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.put('/habits/{pk}', summary='更新习惯', dependencies=[DependsJwtAuth])
async def app_update_habit(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.update_habit(db, owner=owner, pk=pk, data=body)
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


@router.delete('/habits/{pk}', summary='删除习惯', dependencies=[DependsJwtAuth])
async def app_delete_habit(
    request: Request, db: CurrentSessionTransaction, pk: Annotated[int, Path(ge=1)]
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    await plan_service.delete_habit(db, owner=owner, pk=pk)
    await _bump_plan_sync(db, owner)
    return response_base.success(data={'deleted': True})


@router.post('/habits/{pk}/checkin', summary='打卡', dependencies=[DependsJwtAuth])
async def app_checkin_habit(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await plan_service.checkin_habit(db, owner=owner, habit_id=pk, data=body)
    await _bump_plan_sync(db, owner)
    return response_base.success(data=data)


# ── preference + today ───────────────────────────────────────────────────────────
@router.get('/preference', summary='排程偏好', dependencies=[DependsJwtAuth])
async def app_get_preference(request: Request, db: CurrentSession) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    return response_base.success(data=await plan_service.get_preference(db, owner=owner))


@router.put('/preference', summary='更新排程偏好', dependencies=[DependsJwtAuth])
async def app_upsert_preference(
    request: Request, db: CurrentSessionTransaction, body: Annotated[dict[str, Any], Body()]
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    return response_base.success(data=await plan_service.upsert_preference(db, owner=owner, data=body))


@router.get('/today', summary='今日首屏聚合（§10.2）', dependencies=[DependsJwtAuth])
async def app_today(
    request: Request,
    db: CurrentSession,
    start: Annotated[datetime, Query(description='当日起 ISO datetime（用户时区）')],
    end: Annotated[datetime, Query(description='当日止 ISO datetime（用户时区）')],
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    return response_base.success(data=await plan_service.today_overview(db, owner=owner, day_start=start, day_end=end))

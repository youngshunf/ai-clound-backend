"""规划与目标管理 Agent 端 API（P1）。

路由前缀: /api/v1/plan/agent
认证方式: Agent JWT（身份取自 JWT claims，绝不读请求体身份）。owner 取自 ``agent.owner_hasn_id``。
scope 闸（与 scopes.py / 未来 hasn-mcp plan.rs 对齐）：
- 读类（list/get/today）无 required scope（确定性读，不设假闸门）；
- 写类（建/改/删/排期/打卡）→ ``plan:write``（出厂 Allow，owner 三态可覆盖）。

与 app 端共用同一 ``plan_service``，仅身份来源不同（agent 代主人 → owner=agent.owner_hasn_id）。
分身经 hasn-mcp `BackendGateway::for_agent` 调这些端点管理主人的规划数据（P2 接 MCP 工具面）。
"""

import logging

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_plan.service.plan_app_service import plan_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth, check_scopes
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()
log = logging.getLogger(__name__)

_SCOPE_WRITE = 'plan:write'


async def _bump_plan_sync(db: AsyncSession, owner_hasn_id: str) -> None:
    """分身写点后 → WSPUSH ``hasn.sync.invalidate(plan)`` 给该 owner 在线节点（best-effort）。"""
    try:
        from backend.app.hasn.service import sync_invalidate_service as siv

        kind = getattr(siv, 'KIND_PLAN', None)
        if kind is not None:
            await siv.bump(kind, db, owner_id=owner_hasn_id)
    except Exception as e:
        log.warning('[plan] agent sync invalidate 推送失败 (非致命): %s', e)


# ── goal ──────────────────────────────────────────────────────────────────────
@router.get('/goals', summary='列目标')
async def agent_list_goals(
    db: CurrentSession, status: Annotated[str | None, Query()] = None, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    return response_base.success(data=await plan_service.list_goals(db, owner=agent.owner_hasn_id, status=status))


@router.post('/goals', summary='建目标')
async def agent_create_goal(
    db: CurrentSessionTransaction,
    body: Annotated[dict[str, Any], Body()],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await plan_service.create_goal(db, owner=agent.owner_hasn_id, data=body)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data=data)


@router.get('/goals/{pk}', summary='目标详情（含 KR）')
async def agent_get_goal(
    db: CurrentSession, pk: Annotated[int, Path(ge=1)], agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    return response_base.success(data=await plan_service.get_goal(db, owner=agent.owner_hasn_id, pk=pk))


@router.put('/goals/{pk}', summary='改目标')
async def agent_update_goal(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await plan_service.update_goal(db, owner=agent.owner_hasn_id, pk=pk, data=body)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data=data)


@router.delete('/goals/{pk}', summary='删目标')
async def agent_delete_goal(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(ge=1)], agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    await plan_service.delete_goal(db, owner=agent.owner_hasn_id, pk=pk)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data={'deleted': True})


@router.post('/goals/{pk}/key-results', summary='加 KR')
async def agent_create_kr(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await plan_service.create_kr(db, owner=agent.owner_hasn_id, goal_id=pk, data=body)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data=data)


# ── plan ──────────────────────────────────────────────────────────────────────
@router.get('/plans', summary='列计划')
async def agent_list_plans(
    db: CurrentSession,
    status: Annotated[str | None, Query()] = None,
    goal_id: Annotated[int | None, Query()] = None,
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    return response_base.success(
        data=await plan_service.list_plans(db, owner=agent.owner_hasn_id, status=status, goal_id=goal_id)
    )


@router.post('/plans', summary='建计划')
async def agent_create_plan(
    db: CurrentSessionTransaction,
    body: Annotated[dict[str, Any], Body()],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await plan_service.create_plan(db, owner=agent.owner_hasn_id, data=body)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data=data)


@router.get('/plans/{pk}', summary='计划详情（含里程碑）')
async def agent_get_plan(
    db: CurrentSession, pk: Annotated[int, Path(ge=1)], agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    return response_base.success(data=await plan_service.get_plan(db, owner=agent.owner_hasn_id, pk=pk))


@router.put('/plans/{pk}', summary='改计划')
async def agent_update_plan(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await plan_service.update_plan(db, owner=agent.owner_hasn_id, pk=pk, data=body)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data=data)


@router.delete('/plans/{pk}', summary='删计划')
async def agent_delete_plan(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(ge=1)], agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    await plan_service.delete_plan(db, owner=agent.owner_hasn_id, pk=pk)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data={'deleted': True})


@router.post('/plans/{pk}/milestones', summary='加里程碑')
async def agent_create_milestone(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await plan_service.create_milestone(db, owner=agent.owner_hasn_id, plan_id=pk, data=body)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data=data)


# ── todo ──────────────────────────────────────────────────────────────────────
@router.get('/todos', summary='列待办')
async def agent_list_todos(
    db: CurrentSession,
    status: Annotated[str | None, Query()] = None,
    actor: Annotated[str | None, Query()] = None,
    plan_id: Annotated[int | None, Query()] = None,
    goal_id: Annotated[int | None, Query()] = None,
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    return response_base.success(
        data=await plan_service.list_todos(
            db, owner=agent.owner_hasn_id, status=status, actor=actor, plan_id=plan_id, goal_id=goal_id
        )
    )


@router.post('/todos', summary='建待办')
async def agent_create_todo(
    db: CurrentSessionTransaction,
    body: Annotated[dict[str, Any], Body()],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await plan_service.create_todo(db, owner=agent.owner_hasn_id, data=body)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data=data)


@router.get('/todos/{pk}', summary='待办详情')
async def agent_get_todo(
    db: CurrentSession, pk: Annotated[int, Path(ge=1)], agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    return response_base.success(data=await plan_service.get_todo(db, owner=agent.owner_hasn_id, pk=pk))


@router.put('/todos/{pk}', summary='改待办')
async def agent_update_todo(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await plan_service.update_todo(db, owner=agent.owner_hasn_id, pk=pk, data=body)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data=data)


@router.delete('/todos/{pk}', summary='删待办')
async def agent_delete_todo(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(ge=1)], agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    await plan_service.delete_todo(db, owner=agent.owner_hasn_id, pk=pk)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data={'deleted': True})


# ── event ─────────────────────────────────────────────────────────────────────
@router.get('/events', summary='列日程（区间）')
async def agent_list_events(
    db: CurrentSession,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    return response_base.success(
        data=await plan_service.list_events(db, owner=agent.owner_hasn_id, start=start, end=end)
    )


@router.post('/events', summary='建日程/时间块')
async def agent_create_event(
    db: CurrentSessionTransaction,
    body: Annotated[dict[str, Any], Body()],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await plan_service.create_event(db, owner=agent.owner_hasn_id, data=body)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data=data)


@router.put('/events/{pk}', summary='改日程')
async def agent_update_event(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await plan_service.update_event(db, owner=agent.owner_hasn_id, pk=pk, data=body)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data=data)


@router.delete('/events/{pk}', summary='删日程')
async def agent_delete_event(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(ge=1)], agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    await plan_service.delete_event(db, owner=agent.owner_hasn_id, pk=pk)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data={'deleted': True})


# ── habit ─────────────────────────────────────────────────────────────────────
@router.get('/habits', summary='列习惯')
async def agent_list_habits(
    db: CurrentSession, status: Annotated[str | None, Query()] = None, agent: AgentTokenPayload = DependsAgentJwtAuth
) -> ResponseModel:
    return response_base.success(data=await plan_service.list_habits(db, owner=agent.owner_hasn_id, status=status))


@router.post('/habits', summary='建习惯')
async def agent_create_habit(
    db: CurrentSessionTransaction,
    body: Annotated[dict[str, Any], Body()],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await plan_service.create_habit(db, owner=agent.owner_hasn_id, data=body)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data=data)


@router.post('/habits/{pk}/checkin', summary='打卡')
async def agent_checkin_habit(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(ge=1)],
    body: Annotated[dict[str, Any], Body()],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    data = await plan_service.checkin_habit(db, owner=agent.owner_hasn_id, habit_id=pk, data=body)
    await _bump_plan_sync(db, agent.owner_hasn_id)
    return response_base.success(data=data)


# ── preference + today ─────────────────────────────────────────────────────────
@router.get('/preference', summary='排程偏好')
async def agent_get_preference(db: CurrentSession, agent: AgentTokenPayload = DependsAgentJwtAuth) -> ResponseModel:
    return response_base.success(data=await plan_service.get_preference(db, owner=agent.owner_hasn_id))


@router.put('/preference', summary='改排程偏好')
async def agent_upsert_preference(
    db: CurrentSessionTransaction,
    body: Annotated[dict[str, Any], Body()],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    check_scopes(agent, [_SCOPE_WRITE])
    return response_base.success(data=await plan_service.upsert_preference(db, owner=agent.owner_hasn_id, data=body))


@router.get('/today', summary='今日首屏聚合（§10.2）')
async def agent_today(
    db: CurrentSession,
    start: Annotated[datetime, Query()],
    end: Annotated[datetime, Query()],
    agent: AgentTokenPayload = DependsAgentJwtAuth,
) -> ResponseModel:
    return response_base.success(
        data=await plan_service.today_overview(db, owner=agent.owner_hasn_id, day_start=start, day_end=end)
    )

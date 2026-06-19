"""会议副驾用户端 API。

路由前缀: /api/v1/copilot/app
认证方式: Owner JWT（owner hasn_id 由登录用户解析，绝不读请求体身份）。
访问控制: **owner 硬隔离**——session/preference 一律按 owner_hasn_id == 登录主人过滤；
不引入 deck 的 resource_share / 企业可见 / ACL（副驾数据是 owner 私有实时会议元数据）。
"""

from datetime import datetime

from fastapi import APIRouter, Request

from backend.app.hasn_copilot.schema.copilot import (
    RebindAgentRequest,
    SetProjectionRequest,
    UpdatePreferenceRequest,
    UpdateSessionRequest,
    UpsertSessionRequest,
)
from backend.app.hasn_copilot.service.copilot_service import copilot_service
from backend.app.hasn_core import hasn_humans_dao
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


async def _resolve_owner(db: CurrentSession, request: Request) -> str:
    """登录用户 → HASN 主人 hasn_id。"""
    human = await hasn_humans_dao.get_by_user_id(db, request.user.id)
    if not human:
        raise errors.NotFoundError(msg='用户 HASN 身份不存在')
    return human.hasn_id


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise errors.RequestError(msg='非法时间格式（需 ISO8601）') from exc


# ---------- session ----------


@router.get('/sessions', summary='我的副驾会话列表', dependencies=[DependsJwtAuth])
async def list_sessions(request: Request, db: CurrentSession, limit: int = 50, offset: int = 0) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await copilot_service.list_sessions(db, owner_hasn_id=owner, limit=limit, offset=offset)
    return response_base.success(data=data)


@router.put('/sessions', summary='按 session_id upsert 副驾会话（离线起会联网补登）', dependencies=[DependsJwtAuth])
async def upsert_session(request: Request, db: CurrentSessionTransaction, body: UpsertSessionRequest) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await copilot_service.upsert_session(
        db,
        owner_hasn_id=owner,
        session_id=body.session_id,
        bound_agent_id=body.bound_agent_id,
        title=body.title,
        scene=body.scene,
        response_mode=body.response_mode,
        status=body.status,
        source_config=body.source_config,
        started_time=_parse_dt(body.started_time),
    )
    return response_base.success(data=data)


@router.get('/sessions/{session_id}', summary='副驾会话详情', dependencies=[DependsJwtAuth])
async def get_session(request: Request, db: CurrentSession, session_id: str) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await copilot_service.get_session(db, owner_hasn_id=owner, session_id=session_id)
    return response_base.success(data=data)


@router.patch('/sessions/{session_id}', summary='更新会话（response_mode 仅改本场）', dependencies=[DependsJwtAuth])
async def update_session(
    request: Request, db: CurrentSessionTransaction, session_id: str, body: UpdateSessionRequest
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await copilot_service.update_session(
        db,
        owner_hasn_id=owner,
        session_id=session_id,
        bound_agent_id=body.bound_agent_id,
        title=body.title,
        scene=body.scene,
        response_mode=body.response_mode,
        status=body.status,
        source_config=body.source_config,
    )
    return response_base.success(data=data)


@router.post('/sessions/{session_id}/projection', summary='结束投影回填卡片落点', dependencies=[DependsJwtAuth])
async def set_projection(
    request: Request, db: CurrentSessionTransaction, session_id: str, body: SetProjectionRequest
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await copilot_service.set_projection(
        db,
        owner_hasn_id=owner,
        session_id=session_id,
        projection_conversation_id=body.projection_conversation_id,
        projection_message_id=body.projection_message_id,
        end_session=body.end_session,
    )
    return response_base.success(data=data)


# ---------- preference ----------


@router.get('/preference', summary='我的副驾偏好（无则返出厂默认）', dependencies=[DependsJwtAuth])
async def get_preference(request: Request, db: CurrentSession) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await copilot_service.get_preference(db, owner_hasn_id=owner)
    return response_base.success(data=data)


@router.put('/preference', summary='更新副驾偏好（改默认分身/模式/自动纪要）', dependencies=[DependsJwtAuth])
async def update_preference(
    request: Request, db: CurrentSessionTransaction, body: UpdatePreferenceRequest
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await copilot_service.update_preference(
        db,
        owner_hasn_id=owner,
        default_agent_id=body.default_agent_id,
        default_response_mode=body.default_response_mode,
        auto_summary=body.auto_summary,
    )
    return response_base.success(data=data)


@router.post('/preference/rebind-agent', summary='改绑默认协作分身（二次确认后）', dependencies=[DependsJwtAuth])
async def rebind_agent(request: Request, db: CurrentSessionTransaction, body: RebindAgentRequest) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await copilot_service.rebind_default_agent(
        db, owner_hasn_id=owner, agent_id=body.agent_id, also_session_id=body.also_session_id
    )
    return response_base.success(data=data)

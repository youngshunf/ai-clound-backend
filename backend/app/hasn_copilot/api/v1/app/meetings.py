"""会议副驾 v5 会议结果域 用户端 API。

路由前缀: /api/v1/copilot/app（会议路径 /meetings*）
认证方式: Owner JWT（owner hasn_id 由登录用户解析，绝不读请求体身份）。
访问控制: **owner 硬隔离**——所有会议一律按 owner_hasn_id == 登录主人过滤。
分享（/share）走通用 hasn_resource_share（resource_type='meeting'，照抄 deck）。

daemon 调用方: hasn-node `apps/daemon/src/domains/copilot/cloud.rs`（Owner JWT 通道）。
所有端点统一信封 `ResponseModel` + `response_base.success(data=...)`，字段名与 daemon
`meetings_mirror::from_cloud` 精确对齐（绝不裸返 schema，否则 daemon transport 解析炸）。
"""

from fastapi import APIRouter, Request

from backend.app.hasn_copilot.schema.meetings import (
    AddMediaRequest,
    CreateMeetingRequest,
    PatchMeetingRequest,
    PutSegmentsRequest,
    ShareMeetingRequest,
    ShareRevokeRequest,
    WriteMinutesRequest,
)
from backend.app.hasn_copilot.service.meetings_service import meetings_service
from backend.app.hasn_core import hasn_humans_dao
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


async def _resolve_owner(db: CurrentSession, request: Request) -> str:
    """登录用户 → HASN 主人 hasn_id（owner 隔离键，绝不从请求体读身份）。"""
    human = await hasn_humans_dao.get_by_user_id(db, request.user.id)
    if not human:
        raise errors.NotFoundError(msg='用户 HASN 身份不存在')
    return human.hasn_id


# ---------- 会议主档 ----------


@router.post('/meetings', summary='起会建行（按 session_id upsert）', dependencies=[DependsJwtAuth])
async def create_meeting(request: Request, db: CurrentSessionTransaction, body: CreateMeetingRequest) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await meetings_service.create_meeting(
        db,
        owner_hasn_id=owner,
        session_id=body.session_id,
        agent_hasn_id=body.agent_hasn_id,
        title=body.title,
        scene=body.scene,
        node_id=body.node_id,
        started_at=body.started_at,
    )
    return response_base.success(data=data)


@router.get('/meetings', summary='我的会议列表', dependencies=[DependsJwtAuth])
async def list_meetings(request: Request, db: CurrentSession, limit: int = 50, offset: int = 0) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await meetings_service.list_meetings(db, owner_hasn_id=owner, limit=limit, offset=offset)
    return response_base.success(data=data)


@router.get('/meetings/{meeting_id}', summary='会议详情（owner 全量）', dependencies=[DependsJwtAuth])
async def get_meeting(request: Request, db: CurrentSession, meeting_id: str) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await meetings_service.get_detail(db, owner_hasn_id=owner, meeting_id=meeting_id)
    return response_base.success(data=data)


@router.patch('/meetings/{meeting_id}', summary='改会议字段', dependencies=[DependsJwtAuth])
async def patch_meeting(
    request: Request, db: CurrentSessionTransaction, meeting_id: str, body: PatchMeetingRequest
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await meetings_service.patch_meeting(
        db, owner_hasn_id=owner, meeting_id=meeting_id, patch=body.model_dump(exclude_unset=True)
    )
    return response_base.success(data=data)


@router.delete('/meetings/{meeting_id}', summary='整场删除（scope=all|local_media）', dependencies=[DependsJwtAuth])
async def delete_meeting(
    request: Request, db: CurrentSessionTransaction, meeting_id: str, scope: str = 'all'
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await meetings_service.delete_meeting(db, owner_hasn_id=owner, meeting_id=meeting_id, scope=scope)
    return response_base.success(data=data)


# ---------- 转写定稿 / 纪要 ----------


@router.put('/meetings/{meeting_id}/segments', summary='转写定稿幂等上推', dependencies=[DependsJwtAuth])
async def put_segments(
    request: Request, db: CurrentSessionTransaction, meeting_id: str, body: PutSegmentsRequest
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await meetings_service.put_segments(
        db,
        owner_hasn_id=owner,
        meeting_id=meeting_id,
        record_version=body.record_version,
        segments=[s.model_dump() for s in body.segments],
    )
    return response_base.success(data=data)


@router.post('/meetings/{meeting_id}/minutes', summary='纪要写入（幂等 version）', dependencies=[DependsJwtAuth])
async def write_minutes(
    request: Request, db: CurrentSessionTransaction, meeting_id: str, body: WriteMinutesRequest
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await meetings_service.write_minutes(
        db,
        owner_hasn_id=owner,
        meeting_id=meeting_id,
        version=body.version,
        body_md=body.body_md,
        record_view_version=body.record_view_version,
        summary_turn_id=body.summary_turn_id,
    )
    return response_base.success(data=data)


# ---------- 升格媒体 ----------


@router.post('/meetings/{meeting_id}/media', summary='升格媒体 upsert 条目', dependencies=[DependsJwtAuth])
async def add_media(
    request: Request, db: CurrentSessionTransaction, meeting_id: str, body: AddMediaRequest
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await meetings_service.add_media(
        db, owner_hasn_id=owner, meeting_id=meeting_id, media=body.model_dump(exclude_none=True)
    )
    return response_base.success(data=data)


@router.delete('/meetings/{meeting_id}/media/{media_id}', summary='撤销单件升格', dependencies=[DependsJwtAuth])
async def delete_media(
    request: Request, db: CurrentSessionTransaction, meeting_id: str, media_id: str
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await meetings_service.delete_media(db, owner_hasn_id=owner, meeting_id=meeting_id, media_id=media_id)
    return response_base.success(data=data)


# ---------- 分享（通用 resource_share） ----------


@router.post('/meetings/{meeting_id}/share', summary='分享给联系人', dependencies=[DependsJwtAuth])
async def share_meeting(
    request: Request, db: CurrentSessionTransaction, meeting_id: str, body: ShareMeetingRequest
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await meetings_service.share_meeting(
        db,
        owner_hasn_id=owner,
        meeting_id=meeting_id,
        grantee_hasn_id=body.grantee_hasn_id,
        permission=body.permission,
    )
    return response_base.success(data=data)


@router.post('/meetings/{meeting_id}/share/revoke', summary='撤销联系人访问', dependencies=[DependsJwtAuth])
async def share_revoke(
    request: Request, db: CurrentSessionTransaction, meeting_id: str, body: ShareRevokeRequest
) -> ResponseModel:
    owner = await _resolve_owner(db, request)
    data = await meetings_service.share_revoke(
        db, owner_hasn_id=owner, meeting_id=meeting_id, grantee_hasn_id=body.grantee_hasn_id
    )
    return response_base.success(data=data)

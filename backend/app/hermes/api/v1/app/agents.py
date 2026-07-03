from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from backend.app.hermes.service.hermes_agent_app_service import hermes_agent_app_service
from backend.app.hermes.service.hermes_runtime_client import HermesRuntimeError
from backend.common.response.response_code import CustomResponse
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import (
    CurrentSession,
    CurrentSessionTransaction,
)

router = APIRouter()


class CreateAgentPayload(BaseModel):
    agent_name: str = Field(..., min_length=1, max_length=40)
    template: str = Field('assistant', pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    timezone: str = Field('Asia/Shanghai')
    soul: str | None = None
    user_profile: str | None = None
    auto_start_gateway: bool = True
    llm_mode: str = Field('platform', pattern='^(platform|byok)$')
    runtime_type: Literal['hermes'] = 'hermes'
    runtime_location: Literal['local'] = 'local'
    endpoint_ref: str | None = None
    auth_ref: str | None = None
    tenant_ref: str | None = None

    @field_validator('endpoint_ref', 'auth_ref', 'tenant_ref')
    @classmethod
    def validate_secret_ref(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith('secret://'):
            raise ValueError('runtime references must use secret://')
        return value


class UpdateAgentPayload(BaseModel):
    agent_name: str | None = Field(None, min_length=1, max_length=40)
    timezone: str | None = None


class PersonaPayload(BaseModel):
    content: str = Field(...)


class QRStartPayload(BaseModel):
    ttl_seconds: int = Field(300, ge=30, le=3600)


def _trace_id(request: Request) -> str:
    return request.headers.get('X-Request-ID') or uuid4().hex


def _runtime_fail(exc: HermesRuntimeError, msg: str = 'Hermes Runtime 调用失败') -> ResponseModel:
    return response_base.fail(res=CustomResponse(code=400, msg=msg), data=exc.to_response_data())


@router.get('', summary='Agent 列表', dependencies=[DependsJwtAuth])
async def list_agents(
    request: Request,
    db: CurrentSession,
    status: str | None = None,
    channel: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResponseModel:
    data = await hermes_agent_app_service.list_agents(
        db, user_id=request.user.id, status=status, channel=channel, page=page, size=size
    )
    return response_base.success(data=data)


@router.post('', summary='创建 Agent', dependencies=[DependsJwtAuth])
async def create_agent(
    request: Request,
    db: CurrentSessionTransaction,
    payload: CreateAgentPayload,
) -> ResponseModel:
    try:
        data = await hermes_agent_app_service.create_agent(
            db,
            user_id=request.user.id,
            payload=payload,
            trace_id=_trace_id(request),
        )
        return response_base.success(data=data)
    except HermesRuntimeError as exc:
        return _runtime_fail(exc, msg='Agent 创建失败')


@router.get('/{agent_id}', summary='Agent 详情', dependencies=[DependsJwtAuth])
async def get_agent(request: Request, db: CurrentSession, agent_id: Annotated[str, Path()]) -> ResponseModel:
    return response_base.success(
        data=await hermes_agent_app_service.get_agent_detail(db, user_id=request.user.id, agent_id=agent_id)
    )


@router.patch('/{agent_id}', summary='修改 Agent 基础信息', dependencies=[DependsJwtAuth])
async def update_agent(
    request: Request, db: CurrentSessionTransaction, agent_id: Annotated[str, Path()], payload: UpdateAgentPayload
) -> ResponseModel:
    try:
        data = await hermes_agent_app_service.update_agent(
            db, user_id=request.user.id, agent_id=agent_id, payload=payload, trace_id=_trace_id(request)
        )
        return response_base.success(data=data)
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.delete('/{agent_id}', summary='删除 Agent', dependencies=[DependsJwtAuth])
async def delete_agent(
    request: Request,
    db: CurrentSessionTransaction,
    agent_id: Annotated[str, Path()],
) -> ResponseModel:
    try:
        data = await hermes_agent_app_service.delete_agent(
            db,
            user_id=request.user.id,
            agent_id=agent_id,
            trace_id=_trace_id(request),
        )
        return response_base.success(data=data)
    except HermesRuntimeError as exc:
        return _runtime_fail(exc, msg='Agent 删除失败')


@router.get('/{agent_id}/soul', summary='读取 SOUL', dependencies=[DependsJwtAuth])
async def get_soul(request: Request, db: CurrentSession, agent_id: Annotated[str, Path()]) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.get_persona(db, user_id=request.user.id, agent_id=agent_id, kind='soul', trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.put('/{agent_id}/soul', summary='更新 SOUL', dependencies=[DependsJwtAuth])
async def put_soul(request: Request, db: CurrentSessionTransaction, agent_id: Annotated[str, Path()], payload: PersonaPayload) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.put_persona(db, user_id=request.user.id, agent_id=agent_id, kind='soul', content=payload.content, trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.get('/{agent_id}/user-profile', summary='读取 USER', dependencies=[DependsJwtAuth])
async def get_user_profile(request: Request, db: CurrentSession, agent_id: Annotated[str, Path()]) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.get_persona(db, user_id=request.user.id, agent_id=agent_id, kind='user-profile', trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.put('/{agent_id}/user-profile', summary='更新 USER', dependencies=[DependsJwtAuth])
async def put_user_profile(request: Request, db: CurrentSessionTransaction, agent_id: Annotated[str, Path()], payload: PersonaPayload) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.put_persona(db, user_id=request.user.id, agent_id=agent_id, kind='user-profile', content=payload.content, trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.get('/{agent_id}/channels', summary='渠道列表', dependencies=[DependsJwtAuth])
async def channels(request: Request, db: CurrentSession, agent_id: Annotated[str, Path()]) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.channels(db, user_id=request.user.id, agent_id=agent_id, trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.post('/{agent_id}/channels/{channel}/qr/start', summary='发起渠道 QR 绑定', dependencies=[DependsJwtAuth])
async def channel_qr_start(request: Request, db: CurrentSessionTransaction, agent_id: str, channel: str, payload: QRStartPayload) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.channel_action(db, user_id=request.user.id, agent_id=agent_id, channel=channel, action='qr_start', payload=payload.model_dump(), trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.get('/{agent_id}/channels/{channel}/qr/{session_id}/status', summary='渠道 QR 状态', dependencies=[DependsJwtAuth])
async def channel_qr_status(request: Request, db: CurrentSessionTransaction, agent_id: str, channel: str, session_id: str) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.channel_action(db, user_id=request.user.id, agent_id=agent_id, channel=channel, action='qr_status', session_id=session_id, trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.post('/{agent_id}/channels/{channel}/manual', summary='手动绑定渠道', dependencies=[DependsJwtAuth])
async def channel_manual(request: Request, db: CurrentSessionTransaction, agent_id: str, channel: str, payload: dict[str, Any]) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.channel_action(db, user_id=request.user.id, agent_id=agent_id, channel=channel, action='manual', payload=payload, trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.post('/{agent_id}/channels/{channel}/test', summary='测试渠道', dependencies=[DependsJwtAuth])
async def channel_test(request: Request, db: CurrentSessionTransaction, agent_id: str, channel: str, payload: dict[str, Any] | None = None) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.channel_action(db, user_id=request.user.id, agent_id=agent_id, channel=channel, action='test', payload=payload or {}, trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.post('/{agent_id}/channels/{channel}/unbind', summary='解绑渠道', dependencies=[DependsJwtAuth])
async def channel_unbind(request: Request, db: CurrentSessionTransaction, agent_id: str, channel: str, payload: dict[str, Any] | None = None) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.channel_action(db, user_id=request.user.id, agent_id=agent_id, channel=channel, action='unbind', payload=payload or {}, trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.get('/{agent_id}/gateway/status', summary='Gateway 状态', dependencies=[DependsJwtAuth])
async def gateway_status(request: Request, db: CurrentSession, agent_id: str) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.gateway(db, user_id=request.user.id, agent_id=agent_id, action='status', trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.post('/{agent_id}/gateway/{action}', summary='Gateway 操作', dependencies=[DependsJwtAuth])
async def gateway_action(request: Request, db: CurrentSessionTransaction, agent_id: str, action: str) -> ResponseModel:
    if action not in {'start', 'restart', 'stop'}:
        return response_base.fail(res=CustomResponse(code=404, msg='Gateway 操作不存在'))
    try:
        return response_base.success(data=await hermes_agent_app_service.gateway(db, user_id=request.user.id, agent_id=agent_id, action=action, trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.get('/{agent_id}/gateway/logs', summary='Gateway 日志', dependencies=[DependsJwtAuth])
async def gateway_logs(request: Request, db: CurrentSession, agent_id: str, limit: Annotated[int, Query(ge=1, le=1000)] = 100) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.gateway(db, user_id=request.user.id, agent_id=agent_id, action='logs', trace_id=_trace_id(request), limit=limit))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.get('/{agent_id}/workspace/status', summary='Workspace 状态', dependencies=[DependsJwtAuth])
async def workspace_status(request: Request, db: CurrentSession, agent_id: str) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.workspace_status(db, user_id=request.user.id, agent_id=agent_id, trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.get('/{agent_id}/chat/history', summary='Web Chat 历史', dependencies=[DependsJwtAuth])
async def hermes_chat_history(request: Request, db: CurrentSession, agent_id: str) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.chat_history(db, user_id=request.user.id, agent_id=agent_id, trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.post('/{agent_id}/chat/completions', summary='Web Chat', dependencies=[DependsJwtAuth])
async def hermes_chat_completions(
    request: Request,
    db: CurrentSessionTransaction,
    agent_id: str,
    payload: dict[str, Any],
):
    """OpenAI-compat chat completion. payload.stream=True → SSE 流式（M1 §5.5）；
    否则保持 ResponseModel 一次性返回（向后兼容）。
    """
    if payload.get('stream') is True:
        async def _gen():
            try:
                async for chunk in hermes_agent_app_service.stream_chat_completions(
                    db, user_id=request.user.id, agent_id=agent_id, payload=payload,
                    trace_id=_trace_id(request),
                ):
                    yield chunk
            except HermesRuntimeError as exc:
                import json as _json
                yield (
                    'event: error\ndata: '
                    + _json.dumps(exc.to_response_data())
                    + '\n\n'
                ).encode()
        return StreamingResponse(_gen(), media_type='text/event-stream')

    try:
        return response_base.success(data=await hermes_agent_app_service.chat_completions(db, user_id=request.user.id, agent_id=agent_id, payload=payload, trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.post('/{agent_id}/runs', summary='创建 Run', dependencies=[DependsJwtAuth])
async def hermes_create_run(request: Request, db: CurrentSessionTransaction, agent_id: str, payload: dict[str, Any]) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.create_run(db, user_id=request.user.id, agent_id=agent_id, payload=payload, trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)


@router.get('/{agent_id}/runs/{run_id}/events', summary='Run Events', dependencies=[DependsJwtAuth])
async def hermes_run_events(request: Request, db: CurrentSession, agent_id: str, run_id: str) -> ResponseModel:
    try:
        return response_base.success(data=await hermes_agent_app_service.get_run_events(db, user_id=request.user.id, agent_id=agent_id, run_id=run_id, trace_id=_trace_id(request)))
    except HermesRuntimeError as exc:
        return _runtime_fail(exc)

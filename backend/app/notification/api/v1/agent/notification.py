"""统一通知 Agent 端 API

路由前缀: /api/v1/notifications/agent
认证方式: Agent JWT（Authorization: Bearer <agent_jwt>，身份取自 JWT claims）

P1 范围：
- Agent 读取/已读自己收到的通知（recipient = agent_hasn_id，身份恒取自 JWT）。
- Agent 通知自己的主人 emit（source.kind=agent，recipient 限定为本 Agent 的主人）——
  这是 emit() 管线经 Agent JWT 面的安全子集；App/外部源带 manifest 白名单 + 限频的
  广义 emit 见 P5（§7）。

注意：FastAPI 端点签名里的 AgentTokenPayload/CurrentSession/EmitRequest 需运行时解析依赖，
故本文件不使用 `from __future__ import annotations`（与社区 Agent API 同惯例）。
"""
from typing import Annotated

from fastapi import APIRouter

from backend.app.notification.schema.notification import AppEmitRequest, EmitRequest
from backend.app.notification.service.notification_service import notification_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


def _split_csv(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(',') if v.strip()] if value else None


@router.get('/notifications', name='notif_agent_list', summary='Agent 读取自己的通知')
async def list_notifications(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    category: str | None = None,
    type: str | None = None,
    unread_only: bool = False,
    cursor: str | None = None,
    limit: int = 20,
) -> ResponseModel:
    result = await notification_service.list_notifications(
        db,
        recipient_hasn_id=agent.agent_hasn_id,
        categories=_split_csv(category),
        types=_split_csv(type),
        unread_only=unread_only,
        cursor=cursor,
        limit=limit,
    )
    return response_base.success(data=result)


@router.get('/notifications/unread-count', name='notif_agent_unread_count', summary='Agent 未读通知数')
async def unread_count(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
) -> ResponseModel:
    result = await notification_service.unread_count(db, recipient_hasn_id=agent.agent_hasn_id)
    return response_base.success(data=result)


@router.put('/notifications/read-all', name='notif_agent_read_all', summary='Agent 全部已读')
async def read_all(
    db: CurrentSessionTransaction,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    category: str | None = None,
    type: str | None = None,
) -> ResponseModel:
    affected = await notification_service.mark_all_read(
        db,
        recipient_hasn_id=agent.agent_hasn_id,
        types=_split_csv(type),
        categories=_split_csv(category),
    )
    return response_base.success(data={'affected': affected})


@router.put('/notifications/{notification_id}/read', name='notif_agent_mark_read', summary='Agent 标记单条已读')
async def mark_read(
    db: CurrentSessionTransaction,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    notification_id: int,
) -> ResponseModel:
    await notification_service.mark_read(
        db, recipient_hasn_id=agent.agent_hasn_id, notification_id=notification_id
    )
    return response_base.success()


@router.post('/notifications/emit', name='notif_agent_emit', summary='Agent 通知自己的主人')
async def emit_to_owner(
    db: CurrentSessionTransaction,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    body: EmitRequest,
) -> ResponseModel:
    """Agent emit：P1 仅允许通知本 Agent 的主人（recipient 必须是 owner_hasn_id）。

    source 由服务端按 JWT 身份补全（kind=agent，id=agent_hasn_id），绝不读请求体身份。
    广义 App emit（manifest 白名单 + 限频）见 P5。
    """
    if body.recipient_id != agent.owner_hasn_id:
        raise errors.ForbiddenError(msg='P1 仅允许通知本 Agent 的主人')
    source = {
        'kind': 'agent',
        'id': agent.agent_hasn_id,
        'display_name': agent.agent_name,
        'on_behalf_of': agent.owner_hasn_id,
    }
    notification_id = await notification_service.emit(
        db,
        recipient_id=agent.owner_hasn_id,
        source=source,
        category='agent',
        type=body.type,
        title=body.title,
        body=body.body,
        payload=body.payload,
        priority=body.priority,
        dedupe_key=body.dedupe_key,
        group_key=body.group_key,
        delivery_hint=body.delivery_hint,
    )
    return response_base.success(data={'notification_id': notification_id})


@router.post('/notifications/app-emit', name='notif_agent_app_emit', summary='AI-Native App 发通知给主人')
async def app_emit(
    db: CurrentSessionTransaction,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    body: AppEmitRequest,
) -> ResponseModel:
    """AI-Native App 发通知（§7 / P5，广义 App emit）。

    经 Agent JWT 通道（`backend_gateway.for_agent(agent).notification.emit`）落到本 Agent
    API surface。云端按 App 已发布 manifest 的 `notifications.emit` 声明校验：App 已声明该能力
    + category 在白名单内 + 限频（emit 内对 app 源生效）。source 服务端补全（kind=app,
    id=app_id, on_behalf_of=owner），recipient 恒为本 Agent 的主人（MVP 不跨主人）。
    """
    notification_id = await notification_service.app_emit(
        db,
        app_id=body.app_id,
        owner_hasn_id=agent.owner_hasn_id,
        category=body.category,
        type=body.type,
        title=body.title,
        body=body.body,
        payload=body.payload,
        priority=body.priority,
        dedupe_key=body.dedupe_key,
        group_key=body.group_key,
        want_card=body.card,
    )
    return response_base.success(data={'notification_id': notification_id})

"""HASN 资产 - Agent 端本地原件快照上传 API。

身份只取 Agent JWT；调用方不能指定 owner。原件仅在主人显式分享动作后由 daemon 调用本端点，
进入私有桶并按内容幂等登记。
"""

import hashlib

from typing import Annotated

from fastapi import APIRouter
from sqlalchemy import select

from backend.app.hasn.model import HasnAgents, HasnAssets
from backend.app.hasn.schema.asset_api import (
    DeliverSourceSnapshotParam,
    DeliveredSourceSnapshot,
)
from backend.app.hasn.service.hasn_asset_service import AssetRecord, hasn_asset_service
from backend.app.hasn_im.application import local_gateway
from backend.app.hasn_im.application.errors import ImSendRejected
from backend.app.hasn_im.application.provider import get_im_gateway
from backend.app.hasn_im.ports import (
    ActorKind,
    DeliveryState,
    EnsureDirectConversationCommand,
    SendMessageCommand,
    ServicePrincipal,
)
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.response.response_code import CustomResponseCode
from backend.common.response.response_schema import ResponseSchemaModel
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


def _snapshot_attachment(asset_uri: str, asset: AssetRecord | HasnAssets) -> dict[str, object]:
    """只用云端元数据构造消息附件，绝不携带本机路径或原文件名。"""
    kind = str(asset.kind)
    names = {
        'image': '图坊图片',
        'voice': '图坊音频',
        'file': '图坊文件',
    }
    attachment: dict[str, object] = {
        'uri': asset_uri,
        'kind': kind,
        'mime': str(asset.mime),
        'name': names.get(kind, '图坊文件'),
        'size': int(asset.size_bytes),
    }
    for field in ('width', 'height', 'duration_ms'):
        value = getattr(asset, field)
        if value is not None:
            attachment[field] = int(value)
    return attachment


def _delivery_local_id(agent_hasn_id: str, idempotency_key: str) -> str:
    """把调用方幂等键绑定到认证 Agent，避免跨身份复用全局消息 local_id。"""
    digest = hashlib.sha256(f'{agent_hasn_id}:{idempotency_key}'.encode()).hexdigest()
    return f'imagelab-share:{digest}'


@router.post('/deliver', summary='把私有原件快照幂等投递给单个目标')
async def deliver_local_source_snapshot(
    db: CurrentSession,
    agent: Annotated[AgentTokenPayload, DependsAgentJwtAuth],
    body: DeliverSourceSnapshotParam,
) -> ResponseSchemaModel[DeliveredSourceSnapshot]:
    """执行一次已由本地 Ask 或主人确认放行的逐目标投递。"""
    identity = (
        await db.execute(
            select(HasnAgents.id).where(
                HasnAgents.hasn_id == agent.agent_hasn_id,
                HasnAgents.owner_id == agent.owner_hasn_id,
                HasnAgents.status == 'active',
            )
        )
    ).scalar_one_or_none()
    if identity is None:
        raise errors.ForbiddenError(msg='Agent 身份与主人不匹配')

    asset_id = body.asset_uri.removeprefix('hasn://asset/')
    asset = await hasn_asset_service.get_by_asset_id(db, asset_id)
    if asset is None:
        raise errors.NotFoundError(msg='原件快照不存在')
    if asset.owner_hasn_id != agent.owner_hasn_id:
        raise errors.ForbiddenError(msg='无权投递其他主人的原件快照')
    if asset.access != 'private' or asset.content_sha256 is None:
        raise errors.ConflictError(msg='资产不是可投递的私有原件快照')

    target = await local_gateway.resolve_target(db, body.target)
    if target is None:
        result = DeliveredSourceSnapshot(
            target=body.target,
            idempotency_key=body.idempotency_key,
            status='failed',
            error_code='3001',
            error_message=f'目标 {body.target} 不存在',
        )
    elif target.get('entity_type') in {'human', 'agent'}:
        content = {
            'text': '来自图坊的私有快照',
            'attachments': [_snapshot_attachment(body.asset_uri, asset)],
        }
        principal = ServicePrincipal(
            canonical_sender=agent.agent_hasn_id,
            actor_kind=ActorKind.AGENT,
        )
        gateway = get_im_gateway()
        try:
            conversation = await gateway.ensure_direct_conversation(
                EnsureDirectConversationCommand(peer_hasn_id=str(target['hasn_id'])),
                principal,
            )
            delivered = await gateway.send_message(
                SendMessageCommand(
                    conversation_id=conversation.conversation_id,
                    content=content,
                    content_type={'image': 2, 'file': 3, 'voice': 4}.get(asset.kind, 3),
                    idempotency_key=_delivery_local_id(
                        agent.agent_hasn_id,
                        body.idempotency_key,
                    ),
                    context={'source': 'imagelab_share', 'asset_uri': body.asset_uri},
                ),
                principal,
            )
        except ImSendRejected as exc:
            result = DeliveredSourceSnapshot(
                target=body.target,
                idempotency_key=body.idempotency_key,
                status='failed',
                error_code=str(exc.code),
                error_message=exc.message,
            )
        else:
            accepted = delivered.delivery_state is DeliveryState.ACCEPTED
            result = DeliveredSourceSnapshot(
                target=body.target,
                idempotency_key=body.idempotency_key,
                status='sent' if accepted else 'pending',
                message_id=str(delivered.message_id) if delivered.message_id is not None else None,
                conversation_id=delivered.conversation_id,
                error_code=None if accepted else delivered.delivery_state.value,
                error_message=None if accepted else delivered.suppress_reason or '等待接收方确认',
                deduped=delivered.deduped,
            )
    else:
        routed = await local_gateway.send_to_target(
            db,
            from_id=agent.agent_hasn_id,
            to_target=body.target,
            content={
                'text': '来自图坊的私有快照',
                'attachments': [_snapshot_attachment(body.asset_uri, asset)],
            },
            content_type={'image': 2, 'file': 3, 'voice': 4}.get(asset.kind, 3),
            local_id=_delivery_local_id(agent.agent_hasn_id, body.idempotency_key),
            context={'source': 'imagelab_share', 'asset_uri': body.asset_uri},
        )
        route_status = str(routed.get('status') or '')
        failed = bool(routed.get('error'))
        status = 'failed' if failed else 'sent' if route_status == 'sent' else 'pending'
        result = DeliveredSourceSnapshot(
            target=body.target,
            idempotency_key=body.idempotency_key,
            status=status,
            message_id=str(routed['msg_id']) if routed.get('msg_id') is not None else None,
            conversation_id=(str(routed['conversation_id']) if routed.get('conversation_id') is not None else None),
            error_code=(
                None
                if status == 'sent'
                else str(routed.get('code') or route_status or 'delivery_pending')
            ),
            error_message=(
                None
                if status == 'sent'
                else str(routed.get('message') or routed.get('reason') or '等待接收方确认')
            ),
            deduped=bool(routed.get('deduped')),
        )
    return ResponseSchemaModel[DeliveredSourceSnapshot](
        code=CustomResponseCode.HTTP_200.code,
        msg=CustomResponseCode.HTTP_200.msg,
        data=result,
    )

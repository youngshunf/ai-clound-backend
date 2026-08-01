"""云端节点内部面 API（`/api/v1/hasn/internal/cloud-nodes`）。

调用方只有两个内部组件：**edge 反代**（换 grant）与 **hosting-agent**（回报状态/事件、拉更新清单）。
鉴权是 `Authorization: Bearer <service_endpoint('hosting').token>`——与云端调 agent 用的是同一把
派生令牌，两端无需各配（契约 §3.3）。浏览器永远打不到这一面。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_hosting.constants import ERR_TICKET_INVALID
from backend.app.hasn_hosting.schema.cloud_node import (
    AppendNodeEventRequest,
    RedeemAccessTicketRequest,
    RedeemAccessTicketResponse,
    ReportNodeStatusRequest,
)
from backend.app.hasn_hosting.service.access_ticket_service import access_ticket_service
from backend.app.hasn_hosting.service.cloud_node_service import cloud_node_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.internal_auth import require_hosting_internal_bearer
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter(dependencies=[Depends(require_hosting_internal_bearer)])

_NodeId = Annotated[str, Path(min_length=1, max_length=40, description='云端节点 node_id')]


@router.post(
    '/access-ticket/redeem',
    summary='核销节点访问票据并签发会话授予',
    name='hasn_hosting_internal_redeem_access_ticket',
)
async def redeem_access_ticket(
    obj: RedeemAccessTicketRequest,
) -> ResponseSchemaModel[RedeemAccessTicketResponse]:
    """一次性 GETDEL 票据 → 签 60s grant（§3.4③）。票据无效 / 过期 / 已用一律同一个错误码。"""
    payload = await access_ticket_service.redeem_ticket(obj.ticket)
    if payload is None:
        raise errors.RequestError(msg='票据无效或已使用', data={'error': ERR_TICKET_INVALID})
    grant = access_ticket_service.sign_grant(
        node_id=payload.node_id, owner_hasn_id=payload.owner_hasn_id, user_id=payload.user_id
    )
    return response_base.success(
        data=RedeemAccessTicketResponse(
            node_id=payload.node_id,
            owner_hasn_id=payload.owner_hasn_id,
            host=payload.host,
            grant=grant,
        )
    )


@router.post('/{node_id}/status', summary='hosting-agent 回报节点状态', name='hasn_hosting_internal_report_status')
async def report_node_status(
    db: CurrentSessionTransaction,
    node_id: _NodeId,
    obj: ReportNodeStatusRequest,
) -> ResponseModel:
    """容器事实回报。`online` 会被**拒**——在线只由云端 Redis presence 判定（零 fake）。"""
    data = await cloud_node_service.report_status(
        db,
        node_id=node_id,
        status=obj.status,
        host=obj.host,
        failure_reason=obj.failure_reason,
        failure_detail=obj.failure_detail,
        container_ref=obj.container_ref,
        image_digest=obj.image_digest,
        image_version=obj.image_version,
        last_backup_at=obj.last_backup_at,
    )
    return response_base.success(data=data)


@router.post('/{node_id}/events', summary='hosting-agent 追加节点事件', name='hasn_hosting_internal_append_event')
async def append_node_event(
    db: CurrentSessionTransaction,
    node_id: _NodeId,
    obj: AppendNodeEventRequest,
) -> ResponseModel:
    data = await cloud_node_service.append_event(
        db, node_id=node_id, event_type=obj.event_type, detail=obj.detail
    )
    return response_base.success(data=data)


@router.get('/pending-updates', summary='待滚动更新的节点清单', name='hasn_hosting_internal_pending_updates')
async def pending_updates(db: CurrentSession) -> ResponseModel:
    """hosting-agent 轮询。无已发布镜像时诚实返回 `image_available=false` + 空清单。"""
    data = await cloud_node_service.pending_updates(db)
    return response_base.success(data=data)

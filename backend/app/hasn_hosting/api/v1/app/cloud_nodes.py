"""云端节点 Owner 面 API（`/api/v1/hasn/app/cloud-nodes`，Owner JWT）。

契约 §3.1。WebUI **不直连**本面——桌面端 WebUI 只调 daemon `/api/v1/cloud-nodes/*`，
由 daemon 经 Owner session + BackendGateway 代理到这里（父仓 CLAUDE.md 调用边界）。

安全不变量：
- 授权码**绝不**出现在任何响应里；
- 一切读写都按 `owner_hasn_id` 收敛，越权访问一律 404（不泄露他人节点是否存在）；
- `status='online'` 只由 Redis presence 决定，容器状态不作数。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn.service.hasn_auth import hasn_auth_from_jwt
from backend.app.hasn_hosting.schema.cloud_node import (
    AccessTicketResponse,
    CloudNodeView,
    CreateCloudNodeRequest,
    DeleteCloudNodeResult,
)
from backend.app.hasn_hosting.service.cloud_node_service import cloud_node_service
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()

_NodeId = Annotated[str, Path(min_length=1, max_length=40, description='云端节点 node_id')]


@router.get('', summary='我的云端节点列表', name='hasn_hosting_app_list_cloud_nodes')
async def list_cloud_nodes(
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseSchemaModel[list[CloudNodeView]]:
    data = await cloud_node_service.list_nodes(db, owner_hasn_id=auth['hasn_id'])
    return response_base.success(data=[CloudNodeView(**item) for item in data])


@router.post('', summary='创建云端节点', name='hasn_hosting_app_create_cloud_node')
async def create_cloud_node(
    db: CurrentSessionTransaction,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
    obj: CreateCloudNodeRequest | None = None,
) -> ResponseSchemaModel[CloudNodeView]:
    """校验订阅+配额 → 预分配 hasn_nodes(node_type='cloud') → 铸授权码 → 建容器。

    授权码只在服务端流向 hosting-agent，**不在返回值里**。
    """
    del obj  # 当前无可调参数；保留入参形状以便后续扩展不破坏客户端契约
    data = await cloud_node_service.create_node(db, user_id=auth['user_id'], owner_hasn_id=auth['hasn_id'])
    return response_base.success(data=CloudNodeView(**data))


@router.get('/{node_id}', summary='云端节点详情与最近事件', name='hasn_hosting_app_get_cloud_node')
async def get_cloud_node(
    db: CurrentSession,
    node_id: _NodeId,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseSchemaModel[CloudNodeView]:
    data = await cloud_node_service.get_node_detail(db, owner_hasn_id=auth['hasn_id'], node_id=node_id)
    return response_base.success(data=CloudNodeView(**data))


@router.post('/{node_id}/start', summary='启动云端节点', name='hasn_hosting_app_start_cloud_node')
async def start_cloud_node(
    db: CurrentSessionTransaction,
    node_id: _NodeId,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseSchemaModel[CloudNodeView]:
    data = await cloud_node_service.start_node(db, owner_hasn_id=auth['hasn_id'], node_id=node_id)
    return response_base.success(data=CloudNodeView(**data))


@router.post('/{node_id}/stop', summary='停止云端节点', name='hasn_hosting_app_stop_cloud_node')
async def stop_cloud_node(
    db: CurrentSessionTransaction,
    node_id: _NodeId,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseSchemaModel[CloudNodeView]:
    """停止只停容器，**数据卷保留**；分身按既有多设备路由走其它在线设备。"""
    data = await cloud_node_service.stop_node(db, owner_hasn_id=auth['hasn_id'], node_id=node_id)
    return response_base.success(data=CloudNodeView(**data))


@router.post('/{node_id}/restart', summary='重启云端节点', name='hasn_hosting_app_restart_cloud_node')
async def restart_cloud_node(
    db: CurrentSessionTransaction,
    node_id: _NodeId,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseSchemaModel[CloudNodeView]:
    data = await cloud_node_service.restart_node(db, owner_hasn_id=auth['hasn_id'], node_id=node_id)
    return response_base.success(data=CloudNodeView(**data))


@router.post('/{node_id}/reauthorize', summary='重新授权云端节点', name='hasn_hosting_app_reauthorize_cloud_node')
async def reauthorize_cloud_node(
    db: CurrentSessionTransaction,
    node_id: _NodeId,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseSchemaModel[CloudNodeView]:
    """仅 `failed(credential_invalid)` 可用：铸新授权码重新注入并重启，**数据卷不动**。"""
    data = await cloud_node_service.reauthorize_node(
        db, user_id=auth['user_id'], owner_hasn_id=auth['hasn_id'], node_id=node_id
    )
    return response_base.success(data=CloudNodeView(**data))


@router.delete('/{node_id}', summary='删除云端节点', name='hasn_hosting_app_delete_cloud_node')
async def delete_cloud_node(
    db: CurrentSessionTransaction,
    node_id: _NodeId,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseSchemaModel[DeleteCloudNodeResult]:
    """先吊销设备凭据，再销毁容器与卷，最后把 `hasn_nodes` 标记退役。"""
    data = await cloud_node_service.delete_node(
        db, user_id=auth['user_id'], owner_hasn_id=auth['hasn_id'], node_id=node_id
    )
    return response_base.success(data=DeleteCloudNodeResult(**data))


@router.post(
    '/{node_id}/access-ticket',
    summary='签发云端节点访问票据',
    name='hasn_hosting_app_issue_cloud_node_access_ticket',
)
async def issue_access_ticket(
    db: CurrentSession,
    node_id: _NodeId,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseSchemaModel[AccessTicketResponse]:
    """签发 60s 一次性票据，浏览器持它到 edge 换会话（§3.4①）。"""
    ticket = await cloud_node_service.issue_access_ticket(
        db, user_id=auth['user_id'], owner_hasn_id=auth['hasn_id'], node_id=node_id
    )
    return response_base.success(
        data=AccessTicketResponse(ticket=ticket.ticket, expires_at=ticket.expires_at, edge_url=ticket.edge_url)
    )

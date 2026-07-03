"""HASN 认证与 Node 注册 REST API

端点：
- POST /hasn/auth/register          注册 HASN 身份（Human + 默认 Agent）
- POST /hasn/auth/register-node     注册节点
- POST /hasn/auth/node-token        签发 Node JWT（如需）
- GET  /hasn/me                     获取当前用户 HASN 身份
- GET  /hasn/me/nodes               我的节点列表
- GET  /hasn/me/agents              我的 Agent 列表（含在线状态）
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from backend.app.hasn.model import HasnHumans
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_nodes import HasnNodes
from backend.app.hasn.service.hasn_auth import (
    hasn_auth_from_jwt,
    issue_node_jwt,
    register_hasn_agent,
    register_hasn_identity,
    register_node,
    reissue_hasn_node_key,
)
from backend.app.hasn.service.hasn_node_bindings_service import hasn_node_bindings_service
from backend.app.hasn.service.ws_router import ws_router
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.jwt import DependsJwtAuth, jwt_authentication
from backend.database.db import CurrentSession, async_db_session

router = APIRouter()


# ─── 请求/响应模型 ───

class RegisterHasnReq(BaseModel):
    name: str = Field(description='显示名称')
    bio: str | None = Field(None, description='个人简介')
    avatar: str | None = Field(None, description='头像 URL')


class RegisterClientReq(BaseModel):
    node_id: str = Field(description='节点唯一ID (设备指纹派生)')
    client_type: str = Field(default='desktop', description='节点类型: desktop/mobile/web/cloud/sdk')
    device_name: str | None = Field(None, description='设备名称')
    device_info: dict | None = Field(None, description='设备信息')


# ─── 注册 HASN 身份 ───

@router.post('/auth/register', summary='注册 HASN 身份')
async def api_register_hasn(
    obj_in: RegisterHasnReq,
    db: CurrentSession,
    _token: str = DependsJwtAuth,
    request: Request = None,
) -> ResponseModel:
    """为当前平台用户注册 HASN 身份（Human + 默认 Agent），幂等"""
    authorization = request.headers.get('Authorization', '')
    token = authorization.replace('Bearer ', '')
    user_info = await jwt_authentication(token)

    result = await register_hasn_identity(
        db=db,
        user_id=user_info.id,
        name=obj_in.name,
        avatar=obj_in.avatar,
        bio=obj_in.bio,
    )

    if not result.get('already_exists'):
        await db.commit()

    response_data = {
        'human': {
            'hasn_id': result['human'].hasn_id,
            'star_id': result['human'].star_id,
            'nickname': result['human'].nickname,
            'avatar': result['human'].avatar,
        },
        'already_exists': result.get('already_exists', False),
    }
    if result.get('agent'):
        response_data['agent'] = {
            'hasn_id': result['agent'].hasn_id,
            'star_id': result['agent'].star_id,
            'display_name': result['agent'].display_name,
            'avatar': result['agent'].avatar,
        }
    if result.get('agent_key'):
        response_data['agent']['agent_key'] = result['agent_key']

    return response_base.success(data=response_data)


# ─── 注册 Agent HASN 身份 ───

class RegisterAgentReq(BaseModel):
    agent_name: str = Field(description='Agent 标识名（目录名，同一 Owner 下唯一）')
    display_name: str = Field(description='Agent 显示名称')
    agent_type: str = Field(default='desktop', description='Agent 类型: desktop | mobile | cloud | web')
    node_id: str | None = Field(None, description='Agent 驻留节点 ID（设备指纹派生）')
    role: str = Field(default='specialist', description='Agent 角色: primary | specialist | service')
    description: str | None = Field(None, description='Agent 描述')
    capabilities: list | None = Field(None, description='能力列表（A2A AgentCard 兼容）')
    avatar: str | None = Field(None, description='CDN 头像 URL')


@router.post('/auth/register-agent', summary='注册 Agent HASN 身份')
async def api_register_agent(
    obj_in: RegisterAgentReq,
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseModel:
    """为当前用户的 Agent 注册 HASN 身份，幂等（同一 owner + agent_name 不重复创建）"""
    result = await register_hasn_agent(
        db=db,
        owner_hasn_id=auth['hasn_id'],
        agent_name=obj_in.agent_name,
        display_name=obj_in.display_name,
        agent_type=obj_in.agent_type,
        node_id=obj_in.node_id,
        role=obj_in.role,
        description=obj_in.description,
        capabilities=obj_in.capabilities,
        avatar=obj_in.avatar,
    )
    if not result.get('already_exists'):
        await db.commit()

    response_data = {
        'hasn_id': result['agent'].hasn_id,
        'star_id': result['agent'].star_id,
        'display_name': result['agent'].display_name,
        'avatar': result['agent'].avatar,
        'agent_name': result['agent'].agent_name,
        'already_exists': result.get('already_exists', False),
    }
    if result.get('agent_key'):
        response_data['agent_key'] = result['agent_key']

    return response_base.success(data=response_data)


# ─── 注册节点 ───

@router.post('/auth/register-node', summary='注册节点')
async def api_register_node(
    obj_in: RegisterClientReq,
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseModel:
    """注册节点，返回 node_id"""
    node = await register_node(
        db=db,
        node_id=obj_in.node_id,
        owner_hasn_id=auth['hasn_id'],
        node_type=obj_in.client_type,
        node_name=obj_in.device_name,
        node_info=obj_in.device_info,
    )
    await db.commit()

    return response_base.success(data={
        'node_id': node.node_id,
        'node_type': node.node_type,
        'node_name': node.node_name,
    })


class ClientTokenReq(BaseModel):
    node_id: str = Field(description='节点 ID')


# ─── 签发 Node JWT（保留为可选能力） ───

@router.post('/auth/node-token', summary='签发 Node JWT')
async def api_node_token(
    obj_in: ClientTokenReq,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseModel:
    """签发 Node JWT（仅保留为可选能力，主路径使用 NodeKey）"""
    node_id = obj_in.node_id
    async with async_db_session() as db:
        result = await db.execute(
            select(HasnNodes).where(
                HasnNodes.node_id == node_id,
                HasnNodes.created_by_owner_id == auth['hasn_id'],
                HasnNodes.status == 'active',
            )
        )
        node = result.scalar_one_or_none()

    if not node:
        return response_base.fail(msg='节点不存在或不属于当前用户')

    token = issue_node_jwt(
        user_hasn_id=auth['hasn_id'],
        node_id=node_id,
        node_type=node.node_type,
        star_id=auth['star_id'],
    )

    return response_base.success(data={
        'node_jwt': token,
        'node_id': node_id,
    })


# ─── 获取当前用户 HASN 身份 ───

@router.get('/me', summary='获取当前用户 HASN 身份')
async def api_get_me(
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseModel:
    async with async_db_session() as db:
        result = await db.execute(
            select(HasnHumans).where(HasnHumans.hasn_id == auth['hasn_id'])
        )
        human = result.scalar_one_or_none()

    if not human:
        return response_base.fail(msg='HASN 身份不存在')

    return response_base.success(data={
        'hasn_id': human.hasn_id,
        'star_id': human.star_id,
        'nickname': human.nickname,
        'bio': human.bio,
        'avatar': human.avatar,
        'status': human.status,
        'contact_policy': human.contact_policy,
        'timezone': human.timezone,
        'tags': human.tags,
        'stats': human.stats,
    })


# ─── 我的节点列表 ───

@router.get('/me/nodes', summary='我的节点列表')
async def api_list_nodes(
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseModel:
    async with async_db_session() as db:
        result = await db.execute(
            select(HasnNodes).where(
                HasnNodes.created_by_owner_id == auth['hasn_id'],
                HasnNodes.status == 'active',
            ).order_by(HasnNodes.created_time.desc())
        )
        nodes = result.scalars().all()

    return response_base.success(data=[
        {
            'node_id': n.node_id,
            'user_id': n.user_id,
            'allowed_owner_hasn_ids': n.allowed_owner_hasn_ids,
            'node_type': n.node_type,
            'node_name': n.node_name,
            'device_fingerprint': n.device_fingerprint,
            'device_platform': n.device_platform,
            'app_version': n.app_version,
            'node_info': n.node_info,
            'capacity': n.capacity,
            'last_seen_at': n.last_seen_at.isoformat() if n.last_seen_at else None,
            'created_time': n.created_time.isoformat() if n.created_time else None,
        }
        for n in nodes
    ])


@router.post('/me/nodes/{node_id}/reissue-key', summary='重新签发 Node Key')
async def api_reissue_node_key(
    node_id: str,
    db: CurrentSession,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseModel:
    node_key = await reissue_hasn_node_key(
        db=db,
        node_id=node_id,
        owner_hasn_id=auth['hasn_id'],
    )
    await db.commit()
    return response_base.success(data={
        'node_id': node_id,
        'node_key': node_key,
    })


# ─── 我的登录设备（设备管理） ───

@router.get('/me/devices', summary='我的登录设备列表')
async def api_list_devices(
    request: Request,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseModel:
    """设备管理页数据源：每台登录设备的 名称/OS/版本/IP 归属地/实时在线/名下在线 Agent/当前设备标记。

    实时在线以 Redis NODE_CONN_KEY 为准；名下 Agent 按其当前所在节点（ENTITY_NODE_KEY）归卡。
    ip_location 可能为空（未知归属地，零 Mock 如实留空）。
    """
    owner_id = auth['hasn_id']
    current_node_id = request.headers.get('X-Node-Id')

    async with async_db_session() as db:
        node_result = await db.execute(
            select(HasnNodes).where(
                HasnNodes.created_by_owner_id == owner_id,
                HasnNodes.status == 'active',
            ).order_by(HasnNodes.last_seen_at.desc().nullslast())
        )
        nodes = node_result.scalars().all()
        agent_result = await db.execute(
            select(HasnAgents).where(
                HasnAgents.owner_id == owner_id,
                HasnAgents.status == 'active',
            )
        )
        agents = agent_result.scalars().all()

    # 名下 Agent → 当前所在节点（实时 presence），按节点归组
    agents_by_node: dict[str, list[dict]] = {}
    for a in agents:
        node_of = await ws_router.get_entity_node(a.hasn_id)
        if not node_of:
            continue
        agents_by_node.setdefault(node_of, []).append({
            'hasn_id': a.hasn_id,
            'display_name': a.display_name,
            'avatar': a.avatar,
        })

    devices = []
    for n in nodes:
        online = await ws_router.is_node_online(n.node_id)
        devices.append({
            'node_id': n.node_id,
            'node_name': n.node_name,
            'node_type': n.node_type,
            'device_platform': n.device_platform,
            'app_version': n.app_version,
            'ip_address': n.ip_address,
            'ip_location': n.ip_location,
            'online': online,
            'is_current': bool(current_node_id) and n.node_id == current_node_id,
            'agents': agents_by_node.get(n.node_id, []),
            'last_seen_at': n.last_seen_at.isoformat() if n.last_seen_at else None,
            'created_time': n.created_time.isoformat() if n.created_time else None,
        })

    return response_base.success(data=devices)


@router.post('/me/devices/{node_id}/logout', summary='远程登出设备')
async def api_logout_device(
    node_id: str,
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseModel:
    """远程登出某台设备：吊销其对本 Owner 的 node-binding（强制重认证）+ 清 presence +
    释放其名下 Agent 供他机接管 + 关闭其 WS（若连接在本进程内）。
    """
    owner_id = auth['hasn_id']

    async with async_db_session() as db:
        result = await db.execute(
            select(HasnNodes).where(
                HasnNodes.node_id == node_id,
                HasnNodes.status == 'active',
            )
        )
        node = result.scalar_one_or_none()
        if node is None:
            raise errors.NotFoundError(msg='设备不存在或已离线')
        owners = node.allowed_owner_hasn_ids or []
        if node.created_by_owner_id != owner_id and owner_id not in owners:
            raise errors.ForbiddenError(msg='无权操作他人设备')

        # 吊销该 node 对本 Owner 的绑定（lease 失效 → 强制重认证）
        await hasn_node_bindings_service.remove_owner_binding(
            db=db, node_id=node_id, owner_id=owner_id,
        )
        await db.commit()

    # 清 presence + 释放名下 Agent + 关闭 WS（若在本进程）
    disconnected = await ws_router.disconnect_node(node_id)

    return response_base.success(data={
        'node_id': node_id,
        'logged_out': True,
        'disconnected_in_process': disconnected,
    })


# ─── 我的 Agent 列表 ───

@router.get('/me/agents', summary='我的 Agent 列表')
async def api_list_agents(
    auth: Annotated[dict, Depends(hasn_auth_from_jwt)],
) -> ResponseModel:
    async with async_db_session() as db:
        result = await db.execute(
            select(HasnAgents).where(
                HasnAgents.owner_id == auth['hasn_id'],
                HasnAgents.status == 'active',
            ).order_by(HasnAgents.created_time.desc())
        )
        agents = result.scalars().all()

    agents_data = []
    for a in agents:
        online = await ws_router.is_agent_online(a.hasn_id)
        agents_data.append({
            'hasn_id': a.hasn_id,
            'star_id': a.star_id,
            'display_name': a.display_name,
            'agent_name': a.agent_name,
            'type': a.type,
            'node_id': a.node_id,
            'avatar': a.avatar,
            'role': a.role,
            'description': a.description,
            'capabilities': a.capabilities,
            'online': online,
            'created_via': a.created_via,
            'created_time': a.created_time.isoformat() if a.created_time else None,
        })

    return response_base.success(data=agents_data)

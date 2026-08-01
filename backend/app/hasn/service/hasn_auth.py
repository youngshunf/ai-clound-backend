"""HASN 认证服务.

提供 HASN 网络的认证能力：
- Human JWT 认证（复用平台 JWT）
- Node JWT 签发/验证（可选能力）
- WS 统一认证（Bearer Token / OwnerKey + X-Node-Id）
- Owner API Key 认证
- Agent API Key 认证
- HASN 身份注册
- Star ID 生成
"""

import hashlib
import secrets
import uuid

from datetime import timedelta
from typing import Any

import jwt

from fastapi import Depends, HTTPException, Request
from fastapi.security.utils import get_authorization_scheme_param
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnHumans, HasnNodes, HasnOwnerApiKeys
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.common.security.jwt import jwt_authentication, jwt_decode
from backend.core.conf import settings
from backend.database.db import async_db_session
from backend.utils.timezone import timezone

# ─── Star ID 生成 ───


async def _next_star_id(db: AsyncSession) -> str:
    """生成下一个数字唤星号（6位起步）"""
    result = await db.execute(
        select(HasnHumans.star_id)
        .where(HasnHumans.star_id.regexp_match(r'^\d+$'))
        .order_by(HasnHumans.id.desc())
        .limit(1)
    )
    last = result.scalar()
    if last:
        return str(int(last) + 1)
    return '100001'


def _generate_agent_key() -> tuple[str, str]:
    """生成 Agent Key (hasn_ak_) 和对应的 SHA256 哈希"""
    raw_key = f'hasn_ak_{secrets.token_urlsafe(48)}'
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash


# _generate_node_key 已废弃（v2.1 简化认证：NodeKey 合并到 Owner 凭据）


def _generate_owner_key() -> tuple[str, str]:
    """生成 Owner API Key (hasn_ok_) 和对应的 SHA256 哈希"""
    raw_key = f'hasn_ok_{secrets.token_urlsafe(48)}'
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash


# ─── Node JWT ───

HASN_CLIENT_JWT_SECRET = settings.TOKEN_SECRET_KEY
HASN_CLIENT_JWT_ALGORITHM = 'HS256'
HASN_CLIENT_JWT_EXPIRE_SECONDS = 60 * 60 * 24  # 24 小时


def issue_node_jwt(
    user_hasn_id: str,
    node_id: str,
    node_type: str,
    star_id: str,
) -> str:
    """签发 Node JWT（可选能力）"""
    now = timezone.now()
    expire = now + timedelta(seconds=HASN_CLIENT_JWT_EXPIRE_SECONDS)
    payload = {
        'sub': user_hasn_id,
        'node_id': node_id,
        'node_type': node_type,
        'star_id': star_id,
        'type': 'node',
        'iss': 'hasn',
        'iat': int(now.timestamp()),
        'exp': int(expire.timestamp()),
    }
    return jwt.encode(payload, HASN_CLIENT_JWT_SECRET, algorithm=HASN_CLIENT_JWT_ALGORITHM)


def verify_node_jwt(token: str) -> dict[str, Any]:
    """验证 Node JWT，返回 payload"""
    try:
        payload = jwt.decode(
            token,
            HASN_CLIENT_JWT_SECRET,
            algorithms=[HASN_CLIENT_JWT_ALGORITHM],
            options={'verify_exp': True},
        )
        if payload.get('type') != 'node':
            raise HTTPException(status_code=401, detail='非 Node JWT')
        if 'node_id' not in payload:
            raise HTTPException(status_code=401, detail='Node JWT 缺少 node_id')
        if 'node_type' not in payload:
            payload['node_type'] = 'desktop'
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail='Node JWT 已过期')
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f'Node JWT 无效: {e}')


# ─── WS 统一认证（v2.1 简化：Bearer / OwnerKey + X-Node-Id） ───


async def authenticate_ws_connection(
    scheme: str,
    credential: str,
    node_id: str,
    node_name: str | None = None,
) -> dict[str, Any]:
    """
    WS 连接统一认证：用 Bearer Token 或 OwnerKey 完成 Node 认证 + 第一个 Owner 绑定。

    支持:
      - Bearer <jwt_access_token>: 桌面端/Web 端
      - OwnerKey hasn_ok_xxx: SDK/第三方接入

    返回: {
        node_id, owner_hasn_id, user_id, node_type, capacity,
        auth_profile, scopes, expires_at
    }
    """

    scheme_lower = scheme.lower()

    if scheme_lower == 'bearer':
        # JWT 验证
        if credential.startswith('hasn_ok_'):
            # 兼容: Bearer hasn_ok_xxx 也当 OwnerKey 处理
            return await _auth_by_owner_key(credential, node_id, node_name)
        return await _auth_by_bearer(credential, node_id, node_name)

    if scheme_lower == 'ownerkey':
        return await _auth_by_owner_key(credential, node_id, node_name)

    raise HTTPException(status_code=401, detail=f'不支持的认证方式: {scheme}，请使用 Bearer 或 OwnerKey')


async def _auth_by_bearer(
    token: str,
    node_id: str,
    node_name: str | None = None,
) -> dict[str, Any]:
    """通过 JWT Bearer Token 认证，解析出 user → hasn_id"""
    from backend.common.log import log

    user_info = await jwt_authentication(token)
    user_id = user_info.id

    async with async_db_session() as db:
        result = await db.execute(select(HasnHumans).where(HasnHumans.user_id == user_id))
        human = result.scalar_one_or_none()

    if not human:
        raise HTTPException(status_code=401, detail='用户尚未注册 HASN 身份')

    # 自动 upsert hasn_nodes
    async with async_db_session() as db:
        node = await register_node(
            db=db,
            node_id=node_id,
            user_id=user_id,
            owner_hasn_id=human.hasn_id,
            node_type='desktop',
            node_name=node_name,
        )
        await db.commit()

    log.info(f'[HASN] WS Bearer 认证成功: user_id={user_id}, hasn_id={human.hasn_id}, node_id={node_id}')
    return {
        'node_id': node_id,
        'owner_hasn_id': human.hasn_id,
        'user_id': user_id,
        'node_type': node.node_type,
        'capacity': node.capacity or 1,
        'auth_profile': 'bearer_token',
        'scopes': {'bind_owner': True, 'register_agent': True},
        'expires_at': (timezone.now() + timedelta(days=7)).isoformat(),
    }


async def _auth_by_owner_key(
    owner_api_key: str,
    node_id: str,
    node_name: str | None = None,
) -> dict[str, Any]:
    """通过 Owner API Key 认证"""
    from backend.common.log import log
    from backend.common.security.owner_key_auth import verify_owner_key_standalone

    async with async_db_session() as db:
        key = await verify_owner_key_standalone(owner_api_key, db)

        owner_hasn_id = key.owner_id
        user_id = key.user_id

        # 自动 upsert hasn_nodes
        node = await register_node(
            db=db,
            node_id=node_id,
            user_id=user_id,
            owner_hasn_id=owner_hasn_id,
            node_type='desktop',
            node_name=node_name,
        )
        await db.commit()

    log.info(f'[HASN] WS OwnerKey 认证成功: owner_id={owner_hasn_id}, node_id={node_id}')
    return {
        'node_id': node_id,
        'owner_hasn_id': owner_hasn_id,
        'user_id': user_id,
        'node_type': node.node_type,
        'capacity': node.capacity or 1,
        'auth_profile': 'owner_api_key',
        'scopes': key.scopes or {'bind_owner': True, 'register_agent': True},
        'expires_at': key.expires_at.isoformat()
        if key.expires_at
        else (timezone.now() + timedelta(days=365)).isoformat(),
    }


# ─── Agent API Key 验证 ───


async def verify_agent_api_key(api_key: str, db: AsyncSession) -> HasnAgents:
    """验证 Agent API Key，返回 Agent 记录"""
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    result = await db.execute(
        select(HasnAgents).where(
            HasnAgents.api_key_hash == key_hash,
            HasnAgents.status == 'active',
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=401, detail='Agent API Key 无效或 Agent 已停用')
    return agent


async def verify_owner_api_key(owner_hasn_id: str, owner_api_key: str, db: AsyncSession) -> HasnOwnerApiKeys:
    """验证 Owner API Key，返回 Key 记录"""
    key_hash = hashlib.sha256(owner_api_key.encode()).hexdigest()
    result = await db.execute(
        select(HasnOwnerApiKeys).where(
            HasnOwnerApiKeys.owner_id == owner_hasn_id,
            HasnOwnerApiKeys.key_hash == key_hash,
            HasnOwnerApiKeys.status == 'active',
        )
    )
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=401, detail='Owner API Key 无效或已停用')
    return key


async def verify_owner_proof(
    owner_id: str,
    owner_proof: dict[str, Any],
    node_id: str | None,
    db: AsyncSession,
) -> dict[str, Any]:
    """验证 Owner Proof，返回标准化结果。"""
    proof_type = owner_proof.get('type', '')
    credential = owner_proof.get('credential', '')
    if not proof_type or not credential:
        raise HTTPException(status_code=401, detail='缺少 owner_proof')

    timezone.now()

    if node_id:
        node_result = await db.execute(
            select(HasnNodes).where(
                HasnNodes.node_id == node_id,
                HasnNodes.status == 'active',
            )
        )
        node = node_result.scalar_one_or_none()
        if not node:
            raise HTTPException(status_code=401, detail='Node 不存在或已停用')
        allowed_owner_hasn_ids = node.allowed_owner_hasn_ids or []
        if allowed_owner_hasn_ids and owner_id not in allowed_owner_hasn_ids:
            raise HTTPException(status_code=403, detail='当前 Node 不允许绑定该 Owner')

    if proof_type == 'owner_api_key':
        # 复用公共 owner key 验证（已含过期检查 + last_used 更新）
        from backend.common.security.owner_key_auth import verify_owner_key_standalone

        key = await verify_owner_key_standalone(credential, db)
        # 额外校验：owner_id 必须匹配
        if key.owner_id != owner_id:
            raise HTTPException(status_code=401, detail='Owner API Key 与 owner_id 不匹配')
        if key.bound_node_id and node_id and key.bound_node_id != node_id:
            raise HTTPException(status_code=401, detail='Owner API Key 与当前 Node 不匹配')
        return {
            'auth_profile': 'owner_api_key',
            'scopes': key.scopes or {'bind_owner': True, 'register_agent': True},
            'expires_at': key.expires_at,
            'key_id': key.key_id,
        }

    if proof_type == 'bearer_token':
        user_info = await jwt_authentication(credential)
        result = await db.execute(
            select(HasnHumans).where(
                HasnHumans.hasn_id == owner_id,
                HasnHumans.user_id == user_info.id,
            )
        )
        human = result.scalar_one_or_none()
        if not human:
            raise HTTPException(status_code=401, detail='Bearer Token 与 owner_id 不匹配')
        jwt_decode(credential)
        # default to 7 days lease for bearer token
        expires_at = timezone.now() + timedelta(days=7)
        return {
            'auth_profile': 'bearer_token',
            'scopes': {'bind_owner': True, 'register_agent': True},
            'expires_at': expires_at,
            'key_id': None,
        }

    raise HTTPException(status_code=401, detail=f'不支持的 owner_proof.type: {proof_type}')


# ─── HASN 身份注册 ───


async def register_hasn_identity(
    db: AsyncSession,
    user_id: int,
    name: str,
    avatar: str | None = None,
    bio: str | None = None,
) -> dict[str, Any]:
    """
    为平台用户注册 HASN 身份（Human + 默认 Agent）

    幂等：若用户已注册，返回已有记录。

    返回: {human: HasnHumans, agent: HasnAgents, api_key: str | None, already_exists: bool}
    """
    # 幂等检查：已注册则直接返回
    result = await db.execute(select(HasnHumans).where(HasnHumans.user_id == user_id))
    existing_human = result.scalar_one_or_none()
    if existing_human:
        # 查找其默认 Agent
        agent_result = await db.execute(
            select(HasnAgents)
            .where(
                HasnAgents.owner_id == existing_human.hasn_id,
            )
            .order_by(HasnAgents.id.asc())
            .limit(1)
        )
        existing_agent = agent_result.scalar_one_or_none()
        return {
            'human': existing_human,
            'agent': existing_agent,
            'api_key': None,  # 已注册不返回 api_key（安全）
            'already_exists': True,
        }

    # 生成 HASN ID 和 Star ID
    # Core/02 §2.1: ^h_[a-z0-9]{16,22}$（hex 全小写 20 字符合规；旧 uuid4() 带连字符共 36 字符违规）
    hasn_id = f'h_{uuid.uuid4().hex[:20]}'
    star_id = await _next_star_id(db)

    # 创建 Human
    human = HasnHumans(
        hasn_id=hasn_id,
        star_id=star_id,
        user_id=user_id,
        nickname=name,
        bio=bio,
        avatar=avatar,
        status='active',
        contact_policy={
            'human_direct': 'open',
            'via_agent': 'allowed',
            'stranger_policy': 'agent_screens_first',
            'commerce_policy': 'discovery_only',
            'service_policy': 'order_auto_create',
            'professional_policy': 'discovery_then_approve',
        },
        stats={'contacts_count': 0, 'agents_count': 0},
    )
    db.add(human)
    await db.flush()

    # 注意：不再自动创建默认 Agent
    # Agent 由客户端按需注册（local 或 cloud），避免创建未部署的幽灵 Agent

    return {
        'human': human,
        'agent': None,
        'api_key': None,
        'already_exists': False,
    }


def _format_created_at(value: Any) -> str:
    """渲染 {{createdAt}} 用的创建时间串（YYYY-MM-DD）。None → 当前时间。"""
    dt = value or timezone.now()
    try:
        return dt.strftime('%Y-%m-%d')
    except Exception:
        return str(dt)


def _render_profile_vars(
    text: str | None,
    *,
    owner_nickname: str,
    owner_id: str,
    display_name: str,
    agent_name: str,
    star_id: str,
    agent_id: str,
    created_at: str,
) -> str | None:
    """建档即替换 Agent persona/记忆模板里的占位符，使落库内容即完整权威 profile。

    Agent 一旦创建，profile 即权威源——替换只在此处（写 hasn_agents 行前）做一次，
    serve/runtime 端不再替换，owner 记忆合并直接写完整正文，绝不残留 {{}}。
    占位符集合与 huanxing-hub/templates/README.md、hasn-node render_agent_template 对齐。
    None / 无占位符则原样返回。
    """
    if not text:
        return text
    replacements = {
        '{{owner_nickname}}': owner_nickname,
        '{{owner_id}}': owner_id,
        '{{display_name}}': display_name,
        '{{agent_name}}': agent_name,
        '{{star_id}}': star_id,
        '{{agent_id}}': agent_id,
        '{{createdAt}}': created_at,
        '{{created_at}}': created_at,
    }
    for token, value in replacements.items():
        text = text.replace(token, value or '')
    return text


async def register_hasn_agent(
    db: AsyncSession,
    owner_hasn_id: str,
    agent_name: str,
    display_name: str,
    agent_type: str = 'desktop',
    node_id: str | None = None,
    role: str = 'specialist',
    description: str | None = None,
    capabilities: list | dict | None = None,
    created_via: str = 'client',
    avatar: str | None = None,
    template_id: str | None = None,
    template_version: str | None = None,
    skills: dict | list | None = None,
    soul_md: str | None = None,
    agents_md: str | None = None,
    user_md: str | None = None,
    memory_md: str | None = None,
    profession: str | None = None,
    builtin_agent_key: str | None = None,
) -> dict[str, Any]:
    """
    为已有 Human 注册新 Agent 的 HASN 身份

    幂等：同一 owner + agent_name 不重复创建

    参数:
      agent_type: desktop | mobile | cloud | web
      node_id: Agent 驻留节点 ID（设备指纹派生）
      role: primary | specialist | service
      description: Agent 描述
      capabilities: A2A AgentCard 兼容能力列表
      avatar: CDN 头像 URL

    返回: {agent: HasnAgents, agent_key: str | None, already_exists: bool}
    """
    # 验证 owner 存在
    owner_result = await db.execute(select(HasnHumans).where(HasnHumans.hasn_id == owner_hasn_id))
    owner = owner_result.scalar_one_or_none()
    if not owner:
        raise HTTPException(status_code=404, detail='owner_hasn_id 对应的 Human 不存在')

    # 建档即替换占位符的上下文（owner 昵称缺省回退 owner_id）。
    owner_nickname = getattr(owner, 'nickname', None) or owner_hasn_id

    def _render_for(
        text: str | None, *, agent_display: str, a_name: str, a_star: str, a_id: str, a_created: str
    ) -> str | None:
        return _render_profile_vars(
            text,
            owner_nickname=owner_nickname,
            owner_id=owner_hasn_id,
            display_name=agent_display,
            agent_name=a_name,
            star_id=a_star,
            agent_id=a_id,
            created_at=a_created,
        )

    # 幂等检查
    existing_result = await db.execute(
        select(HasnAgents).where(
            HasnAgents.owner_id == owner_hasn_id,
            HasnAgents.agent_name == agent_name,
        )
    )
    existing_agent = existing_result.scalar_one_or_none()
    if existing_agent:
        # 建档即替换（幂等回填同样要完整）：用现存 star_id/hasn_id/创建时间渲染占位符，
        # 再参与下面的「变了才更新」比较，避免把带 {{}} 的模板原文回填进权威 profile。
        eff_display = display_name or existing_agent.display_name
        created = _format_created_at(getattr(existing_agent, 'created_time', None))
        soul_md = _render_for(
            soul_md, agent_display=eff_display, a_name=existing_agent.agent_name,
            a_star=existing_agent.star_id, a_id=existing_agent.hasn_id, a_created=created,
        )
        agents_md = _render_for(
            agents_md, agent_display=eff_display, a_name=existing_agent.agent_name,
            a_star=existing_agent.star_id, a_id=existing_agent.hasn_id, a_created=created,
        )
        user_md = _render_for(
            user_md, agent_display=eff_display, a_name=existing_agent.agent_name,
            a_star=existing_agent.star_id, a_id=existing_agent.hasn_id, a_created=created,
        )
        memory_md = _render_for(
            memory_md, agent_display=eff_display, a_name=existing_agent.agent_name,
            a_star=existing_agent.star_id, a_id=existing_agent.hasn_id, a_created=created,
        )
        # 幂等更新：如果属性变了，更新记录
        updated = False
        if node_id and existing_agent.node_id != node_id:
            existing_agent.node_id = node_id
            updated = True
        if display_name and existing_agent.display_name != display_name:
            existing_agent.display_name = display_name
            updated = True
        if agent_type and existing_agent.type != agent_type:
            existing_agent.type = agent_type
            updated = True
        if avatar and existing_agent.avatar != avatar:
            existing_agent.avatar = avatar
            updated = True
        for attr, value in {
            'description': description,
            'profession': profession,
            'capabilities': capabilities,
            'template_id': template_id,
            'template_version': template_version,
            'skills': skills,
            'soul_md': soul_md,
            'agents_md': agents_md,
            'user_md': user_md,
            'memory_md': memory_md,
            'builtin_agent_key': builtin_agent_key,
        }.items():
            if value is not None and hasattr(existing_agent, attr) and getattr(existing_agent, attr) != value:
                setattr(existing_agent, attr, value)
                updated = True
        if updated:
            if hasattr(existing_agent, 'profile_revision'):
                existing_agent.profile_revision = (existing_agent.profile_revision or 1) + 1
            await db.flush()
        return {
            'agent': existing_agent,
            'agent_key': None,
            'already_exists': True,
        }

    # 生成身份
    agent_hasn_id = f'a_{uuid.uuid4().hex[:20]}'  # Core/02 §2.1: ^a_[a-z0-9]{16,22}$（同 human 修正）
    agent_star_id = f'{owner.star_id}#{agent_name}'
    agent_key, agent_key_hash = _generate_agent_key()

    # 建档即替换：star_id/hasn_id 已分配，用真实值渲染 persona/记忆占位符再落库（落库即权威）。
    created = _format_created_at(None)
    soul_md = _render_for(
        soul_md, agent_display=display_name, a_name=agent_name,
        a_star=agent_star_id, a_id=agent_hasn_id, a_created=created,
    )
    agents_md = _render_for(
        agents_md, agent_display=display_name, a_name=agent_name,
        a_star=agent_star_id, a_id=agent_hasn_id, a_created=created,
    )
    user_md = _render_for(
        user_md, agent_display=display_name, a_name=agent_name,
        a_star=agent_star_id, a_id=agent_hasn_id, a_created=created,
    )
    memory_md = _render_for(
        memory_md, agent_display=display_name, a_name=agent_name,
        a_star=agent_star_id, a_id=agent_hasn_id, a_created=created,
    )

    agent = HasnAgents(
        hasn_id=agent_hasn_id,
        star_id=agent_star_id,
        owner_id=owner_hasn_id,
        display_name=display_name,
        agent_name=agent_name,
        type=agent_type,
        node_id=node_id,
        role=role or 'specialist',
        profession=profession,
        builtin_agent_key=builtin_agent_key,
        description=description,
        capabilities=capabilities,
        template_id=template_id,
        template_version=template_version,
        skills=skills,
        soul_md=soul_md,
        agents_md=agents_md,
        user_md=user_md,
        memory_md=memory_md,
        profile_source='cloud',
        profile_revision=1,
        avatar=avatar,
        api_key_hash=agent_key_hash,
        status='active',
        created_via=created_via,
    )
    db.add(agent)
    await db.flush()

    # Agent 身份和投影命令同事务提交；关系 relay 仅经 IM role 的 RelationGateway 建控制边。
    # relay 在 IM 已提交但响应丢失时用稳定幂等键重试，不会生成重复关系。
    from backend.app.hasn.service.hasn_relation_command_outbox_service import (
        hasn_relation_command_outbox_service,
    )

    await hasn_relation_command_outbox_service.enqueue_owner_agent_control_edge(
        db,
        owner_hasn_id=owner_hasn_id,
        agent_hasn_id=agent_hasn_id,
    )

    return {
        'agent': agent,
        'agent_key': agent_key,
        'already_exists': False,
    }


# ─── 节点注册 ───


async def register_node(
    db: AsyncSession,
    node_id: str,
    user_id: int | None = None,
    owner_hasn_id: str | None = None,
    node_type: str = 'desktop',
    node_name: str | None = None,
    node_info: dict | None = None,
    allowed_owner_hasn_ids: list[str] | None = None,
) -> HasnNodes:
    """注册 Node（幂等：按 node_id 去重，由于 node_id 客户端基于设备指纹生成，同一物理设备永远只有一条记录）

    **node_type 只在新建行时取参数值**：已有行保留库里的既有 `node_type`。daemon 全链路不上报
    node_type（握手分支硬写 `'desktop'`），而云端节点的 `node_type='cloud'` 是签发授权码时由云端
    预分配的权威事实。若这里跟着覆盖，容器一上线就会把自己降级成 desktop，权威被客户端默认值冲掉。
    事实源：`docs/hasn-node设计文档/云端节点托管/实施/01-切片实施契约(H1-H8).md` §0.2。
    """
    node_info = node_info or {}
    fingerprint = node_info.get('device_fingerprint')
    device_platform = node_info.get('device_platform') or node_info.get('platform')
    app_version = node_info.get('app_version')
    capacity = int(node_info.get('capacity') or (3 if node_type == 'desktop' else 1))
    created_by_owner_id = owner_hasn_id

    # 幂等检查：按 node_id 查找
    if node_id:
        result = await db.execute(
            select(HasnNodes).where(
                HasnNodes.node_id == node_id,
                HasnNodes.status == 'active',
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            # 更新可变字段
            existing.last_seen_at = timezone.now()
            existing.user_id = user_id or existing.user_id
            if node_name and existing.node_name != node_name:
                existing.node_name = node_name
            # node_type 权威在云端预分配记录，不得被客户端握手的默认值覆盖（契约 §0.2）
            existing.capacity = capacity or existing.capacity
            # 合并 allowed_owner_hasn_ids（多用户同设备）
            if owner_hasn_id:
                current_owners = existing.allowed_owner_hasn_ids or []
                if owner_hasn_id not in current_owners:
                    existing.allowed_owner_hasn_ids = [*current_owners, owner_hasn_id]
            if allowed_owner_hasn_ids is not None:
                existing.allowed_owner_hasn_ids = allowed_owner_hasn_ids
            existing.created_by_owner_id = created_by_owner_id or existing.created_by_owner_id
            existing.device_platform = device_platform or existing.device_platform
            existing.app_version = app_version or existing.app_version
            existing.node_info = node_info or existing.node_info

            # 如果提供了指纹但数据库中没有，则更新进去
            if fingerprint and not existing.device_fingerprint:
                existing.device_fingerprint = fingerprint

            await db.flush()
            return existing

    node = HasnNodes(
        node_id=node_id,
        user_id=user_id,
        allowed_owner_hasn_ids=allowed_owner_hasn_ids or ([owner_hasn_id] if owner_hasn_id else None),
        node_type=node_type,
        node_name=node_name,
        device_fingerprint=fingerprint,
        device_platform=device_platform,
        app_version=app_version,
        node_info=node_info,
        created_by_owner_id=created_by_owner_id,
        capacity=capacity,
        status='active',
    )
    db.add(node)
    await db.flush()
    return node


# ensure_hasn_node_key 已废弃（v2.1 简化认证：NodeKey 合并到 Owner 凭据）


async def ensure_hasn_owner_key(
    db: AsyncSession,
    user_id: int,
    nickname: str,
) -> str | None:
    """
    登录时调用：确保 Owner API Key 已签发（幂等）。

    - 已有 active key → 直接返回 None（明文只在首次创建时返回）
    - 没有 active key → 签发新 key，返回明文 hasn_ok_xxx
    - 失败 → 返回 None（不阻塞登录）

    返回: owner_key (hasn_ok_xxx) 或 None
    """
    from backend.common.log import log

    try:
        # 1. 确保 HASN Human 身份已注册
        identity = await register_hasn_identity(
            db=db,
            user_id=user_id,
            name=nickname,
        )
        hasn_id = identity['human'].hasn_id

        # 2. 检查是否已有 active 的 Owner Key
        existing = await db.execute(
            select(HasnOwnerApiKeys)
            .where(
                HasnOwnerApiKeys.user_id == user_id,
                HasnOwnerApiKeys.status == 'active',
            )
            .limit(1)
        )
        if existing.scalar_one_or_none():
            log.debug(f'[HASN] Owner Key 已存在: user_id={user_id}')
            return None  # 已有 key，明文不可恢复

        # 3. 签发新 Owner Key
        from backend.app.hasn.service.hasn_api_key_service import hasn_api_key_service

        result = await hasn_api_key_service.create_api_key(
            db=db,
            user_id=user_id,
            user_hasn_id=hasn_id,
            name='Auto (login)',
            scopes={'bind_owner': True, 'register_agent': True},
        )
        log.info(f'[HASN] Owner Key 签发 OK: user_id={user_id}, key_id={result.key_id}')
        return result.owner_api_key

    except Exception as e:
        log.warning(f'[HASN] Owner Key 签发失败（不阻塞登录）: {e}')
        return None


async def reissue_hasn_node_key(
    db: AsyncSession,
    node_id: str,
    owner_hasn_id: str,
) -> str:
    """重新签发 Node Key — v2.1 已废弃，保留接口兼容。

    在 v2.1 简化认证之后，Node 不再使用独立的 NodeKey，
    此函数仅为前端设置页面的"重新签发"按钮保留向后兼容。
    """
    raise HTTPException(
        status_code=410,
        detail='Node Key 已废弃 (v2.1)，节点认证现使用 Bearer Token / OwnerKey',
    )


# ─── 认证依赖注入 ───


async def hasn_auth_from_jwt(request: Request) -> dict[str, Any]:
    """
    从平台 JWT 中提取 HASN 身份

    用于 REST API 认证，返回:
    {
        "hasn_id": "h_xxx",
        "star_id": "100001",
        "user_id": 123,
        "auth_type": "jwt"
    }
    """
    authorization = request.headers.get('Authorization')
    if not authorization:
        raise HTTPException(status_code=401, detail='缺少认证信息')

    scheme, credentials = get_authorization_scheme_param(authorization)
    if scheme.lower() != 'bearer':
        raise HTTPException(status_code=401, detail='仅支持 Bearer 认证')

    # 验证平台 JWT
    user_info = await jwt_authentication(credentials)
    user_id = user_info.id

    # 查找 HASN 身份
    async with async_db_session() as db:
        result = await db.execute(select(HasnHumans).where(HasnHumans.user_id == user_id))
        human = result.scalar_one_or_none()

    if not human:
        raise HTTPException(status_code=404, detail='未注册 HASN 身份，请先注册')

    return {
        'hasn_id': human.hasn_id,
        'star_id': human.star_id,
        'user_id': user_id,
        'nickname': human.nickname,
        'auth_type': 'jwt',
    }


async def hasn_auth_from_node_credential(request: Request) -> dict[str, Any]:
    """v2.1: 从 Authorization 中提取 Node 认证信息（支持 Bearer / OwnerKey）。"""
    authorization = request.headers.get('Authorization')
    if not authorization:
        raise HTTPException(status_code=401, detail='缺少认证信息')

    scheme, credentials = get_authorization_scheme_param(authorization)
    node_id = request.headers.get('X-Node-Id', '')
    node_name = request.headers.get('X-Node-Name')
    return await authenticate_ws_connection(scheme, credentials, node_id, node_name)


async def hasn_auth_dual(request: Request) -> dict[str, Any]:
    """
    HASN 双模式认证：Bearer JWT 或 ApiKey

    返回:
    {
        "hasn_id": "h_xxx" 或 "a_xxx",
        "star_id": "100001" 或 "100001#star",
        "entity_type": "human" 或 "agent",
        "auth_type": "jwt" 或 "apikey"
    }
    """
    authorization = request.headers.get('Authorization')
    if not authorization:
        raise HTTPException(status_code=401, detail='缺少认证信息')

    scheme, credentials = get_authorization_scheme_param(authorization)

    if scheme.lower() == 'bearer':
        return await hasn_auth_from_jwt(request)

    if scheme.lower() == 'apikey':
        async with async_db_session() as db:
            agent = await verify_agent_api_key(credentials, db)
        return {
            'hasn_id': agent.hasn_id,
            'star_id': agent.star_id,
            'entity_type': 'agent',
            'owner_id': agent.owner_id,
            'auth_type': 'apikey',
        }

    raise HTTPException(status_code=401, detail=f'不支持的认证方式: {scheme}')


# 依赖注入快捷方式
DependsHasnAuth = Depends(hasn_auth_from_jwt)
DependsHasnDualAuth = Depends(hasn_auth_dual)
DependsHasnNodeAuth = Depends(hasn_auth_from_node_credential)

# 兼容别名（contacts.py 使用此名称）
hasn_auth = hasn_auth_from_jwt

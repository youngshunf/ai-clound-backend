"""
Agent JWT 认证模块

Agent 使用独立的 JWT 进行身份认证，与 Owner JWT 平级但权限受限。
凭证只承载身份（agent_hasn_id / owner），**不再携带 scopes claim**：授权判定的唯一
真相是 ``hasn_agent_scopes.{default_mode, capability_modes}``（消费时活取，doc17 / 实施102 S0）。

认证方式: Header `Authorization: Bearer <agent_jwt>`
Token Type: agent (通过 payload.token_type 区分)

@author Ysf
@date 2026-05-13
"""

import json
import uuid

from datetime import timedelta
from typing import Any

from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.dataclasses import AgentAccessToken, AgentTokenPayload
from backend.common.exception import errors
from backend.common.log import log
from backend.core.conf import settings
from backend.database.redis import redis_client
from backend.utils.timezone import timezone


def jwt_encode_agent(payload: dict[str, Any]) -> str:
    """
    生成 Agent JWT token

    :param payload: 载荷
    :return: JWT token
    """
    return jwt.encode(payload, settings.TOKEN_SECRET_KEY, settings.TOKEN_ALGORITHM)


def jwt_decode_agent(token: str) -> AgentTokenPayload:
    """
    解析 Agent JWT token

    :param token: JWT token
    :return: AgentTokenPayload
    """
    try:
        payload = jwt.decode(
            token,
            settings.TOKEN_SECRET_KEY,
            algorithms=[settings.TOKEN_ALGORITHM],
            options={'verify_exp': True},
        )

        token_type = payload.get('token_type')
        if token_type != 'agent':
            raise errors.TokenError(msg='Token 类型错误')

        agent_hasn_id = payload.get('agent_hasn_id')
        agent_name = payload.get('agent_name')
        owner_hasn_id = payload.get('owner_hasn_id')
        owner_user_id = payload.get('owner_user_id')
        session_uuid = payload.get('session_uuid')
        expire = payload.get('exp')

        if not all([agent_hasn_id, owner_hasn_id, owner_user_id, session_uuid, expire]):
            raise errors.TokenError(msg='Agent Token 无效')

        return AgentTokenPayload(
            agent_hasn_id=agent_hasn_id,
            agent_name=agent_name or '',
            owner_hasn_id=owner_hasn_id,
            owner_user_id=int(owner_user_id),
            session_uuid=session_uuid,
            expire_time=timezone.from_datetime(timezone.to_utc(expire)),
            token_type='agent',
        )
    except errors.TokenError:
        raise
    except Exception as e:
        raise errors.TokenError(msg=f'Agent Token 解析失败: {e!s}')


def is_agent_token(token: str) -> bool:
    """不验签快速判断 Bearer 是否为 Agent JWT（仅用于 Owner JWT 中间件分流）。

    Agent JWT 的 ``token_type='agent'`` 且 ``sub`` 为 ``a_*``（非数字 user_id）；
    Owner JWT 中间件会对其做 ``int(sub)`` 解析必抛 → 401「Token 已失效，请重新登录」，
    请求根本到不了路由自身的 ``DependsAgentJwtAuth``。中间件据此放行所有 Agent 面，
    无需逐路由维护路径白名单。

    安全性：此处仅做「不拿它当 Owner 解」的分流判断，**不基于未验签 claim 授权**；
    真验签 + Redis 吊销检查仍由路由的 ``verify_agent_token`` 完成，伪造 ``token_type``
    的无效 token 会被路由验签拒绝。
    """
    try:
        claims = jwt.get_unverified_claims(token)
    except Exception:
        return False
    return isinstance(claims, dict) and claims.get('token_type') == 'agent'


async def create_agent_access_token(
    agent_hasn_id: str,
    agent_name: str,
    owner_hasn_id: str,
    owner_user_id: int,
) -> AgentAccessToken:
    """
    生成 Agent JWT token

    :param agent_hasn_id: Agent 的 HASN ID
    :param agent_name: Agent 显示名
    :param owner_hasn_id: Owner 的 HASN ID
    :param owner_user_id: Owner 的 user_id
    :return: AgentAccessToken
    """
    expire = timezone.now() + timedelta(seconds=settings.TOKEN_EXPIRE_SECONDS)
    session_uuid = str(uuid.uuid4())

    payload = {
        'sub': agent_hasn_id,
        'token_type': 'agent',
        'agent_hasn_id': agent_hasn_id,
        'agent_name': agent_name,
        'owner_hasn_id': owner_hasn_id,
        'owner_user_id': owner_user_id,
        'session_uuid': session_uuid,
        'exp': timezone.to_utc(expire).timestamp(),
    }

    access_token = jwt_encode_agent(payload)

    # 存储到 Redis
    await redis_client.setex(
        f'agent_token:{agent_hasn_id}:{session_uuid}',
        settings.TOKEN_EXPIRE_SECONDS,
        access_token,
    )

    return AgentAccessToken(
        access_token=access_token,
        access_token_expire_time=expire,
        session_uuid=session_uuid,
    )


async def revoke_agent_token(agent_hasn_id: str, session_uuid: str) -> None:
    """
    吊销 Agent token

    :param agent_hasn_id: Agent 的 HASN ID
    :param session_uuid: 会话 UUID
    :return:
    """
    await redis_client.delete(f'agent_token:{agent_hasn_id}:{session_uuid}')


async def revoke_all_agent_tokens(agent_hasn_id: str) -> None:
    """
    吊销某个 Agent 的所有 token

    :param agent_hasn_id: Agent 的 HASN ID
    :return:
    """
    await redis_client.delete_prefix(f'agent_token:{agent_hasn_id}:')


async def verify_agent_token(token: str) -> AgentTokenPayload:
    """
    验证 Agent JWT token

    :param token: JWT token
    :return: AgentTokenPayload
    """
    token_payload = jwt_decode_agent(token)

    # 检查 Redis 中是否存在（支持主动吊销）
    redis_token = await redis_client.get(f'agent_token:{token_payload.agent_hasn_id}:{token_payload.session_uuid}')

    if not redis_token:
        raise errors.TokenError(msg='Agent Token 已过期或已被吊销')

    if token != redis_token:
        raise errors.TokenError(msg='Agent Token 已失效')

    return token_payload


def _ensure_policy_defaults(config: dict[str, Any]) -> dict[str, Any]:
    """补齐三态字段默认值（兼容 v3 前写入的旧缓存/旧行）。

    三态判定真相是 default_mode + capability_modes（消费时活取，doc17 / 实施102 S0）。
    ``scopes``/``post_needs_review`` 死字段已随 JWT claim 一并退役——旧缓存里若还留着，
    这里顺手剔除，避免下游误读。default_mode 缺失/非法→'allow'（默认全开）。
    """
    config.pop('scopes', None)
    config.pop('post_needs_review', None)
    default_mode = config.get('default_mode')
    if default_mode not in ('allow', 'ask', 'deny'):
        default_mode = 'allow'
    config['default_mode'] = default_mode
    caps = config.get('capability_modes')
    if isinstance(caps, str):
        try:
            caps = json.loads(caps or '{}')
        except (ValueError, TypeError):
            caps = {}
    config['capability_modes'] = caps if isinstance(caps, dict) else {}
    return config


async def get_agent_scopes_from_db(db: AsyncSession, agent_hasn_id: str) -> dict[str, Any]:
    """
    从数据库查询 Agent 的三态授权配置（default_mode + capability_modes）。

    v3（16-doc D-v3-2）起 ``scopes``/``post_needs_review`` 列已 drop，JWT scopes claim
    亦已退役（实施102 S0）：判定只看 default_mode + capability_modes。

    :param db: 数据库会话
    :param agent_hasn_id: Agent 的 HASN ID
    :return: {"default_mode": str, "capability_modes": dict}
    """
    from sqlalchemy import text

    # 查询 hasn_agent_scopes 表（只读三态判定真相列）
    result = await db.execute(
        text("""
            SELECT default_mode, capability_modes
            FROM hasn_agent_scopes
            WHERE agent_hasn_id = :agent_hasn_id
        """),
        {'agent_hasn_id': agent_hasn_id},
    )
    row = result.fetchone()

    if not row:
        # 无记录：默认全开（default_mode='allow'），与新建 Agent 一致（Q3/D1）
        return _ensure_policy_defaults({})

    return _ensure_policy_defaults({
        'default_mode': row[0],
        'capability_modes': row[1],
    })


async def get_agent_scopes_cached(agent_hasn_id: str, db: AsyncSession) -> dict[str, Any]:
    """
    获取 Agent 权限配置（带缓存，三态判定真相）

    :param agent_hasn_id: Agent 的 HASN ID
    :param db: 数据库会话
    :return: {"default_mode": str, "capability_modes": dict}
    """
    cache_key = f'agent_scopes:{agent_hasn_id}'

    # 尝试从 Redis 获取（旧缓存可能缺三态字段，统一补默认）
    cached = await redis_client.get(cache_key)
    if cached:
        return _ensure_policy_defaults(json.loads(cached))

    # 从数据库查询
    scopes_config = await get_agent_scopes_from_db(db, agent_hasn_id)

    # 缓存 1 小时
    await redis_client.setex(
        cache_key,
        3600,
        json.dumps(scopes_config, ensure_ascii=False),
    )

    return scopes_config


async def invalidate_agent_scopes_cache(agent_hasn_id: str) -> None:
    """
    清除 Agent 权限缓存

    :param agent_hasn_id: Agent 的 HASN ID
    :return:
    """
    await redis_client.delete(f'agent_scopes:{agent_hasn_id}')


def _env_privileged_grants(agent_hasn_id: str) -> set[str]:
    """从 ENV PLATFORM_OPERATOR_AGENTS 解析该分身的 bootstrap 授予（仅应急兜底，doc18 §4.1）。

    格式与表行同构：`agent_hasn_id:scope[,agent_hasn_id:scope…]`。scope 自身含 `:`
    （如 diag:read:all），故按**第一个** `:` 切 agent 段、余下整体为 scope。
    """
    raw = (settings.PLATFORM_OPERATOR_AGENTS or '').strip()
    if not raw:
        return set()
    grants: set[str] = set()
    for entry in raw.split(','):
        head, sep, scope = entry.strip().partition(':')
        if sep and head == agent_hasn_id and scope:
            grants.add(scope)
    return grants


async def get_privileged_grants_from_db(db: AsyncSession, agent_hasn_id: str) -> list[str]:
    """查 hasn_platform_operator_grants（G1 特权门唯一授予源，owner 不可自授）。"""
    from sqlalchemy import text

    result = await db.execute(
        text('SELECT scope FROM hasn_platform_operator_grants WHERE agent_hasn_id = :agent_hasn_id'),
        {'agent_hasn_id': agent_hasn_id},
    )
    return [row[0] for row in result.fetchall()]


async def get_privileged_grants_cached(agent_hasn_id: str, db: AsyncSession) -> frozenset[str]:
    """
    获取 Agent 的平台特权授予集（表 ∪ ENV bootstrap，带短 TTL 缓存）。

    授予/撤销经 invalidate_privileged_grants_cache 即时生效（运维授予低频，暂不 WSPUSH）；
    TTL 取 300s 短于三态缓存，兜底缓存失效路径的漏网。

    :return: 授予值集合（精确 scope 或段尾通配，如 diag:read:all / ops:*）
    """
    cache_key = f'privileged_grants:{agent_hasn_id}'

    grants: set[str] = set()
    try:
        cached = await redis_client.get(cache_key)
        if cached is not None:
            grants = set(json.loads(cached))
        else:
            grants = set(await get_privileged_grants_from_db(db, agent_hasn_id))
            await redis_client.setex(cache_key, 300, json.dumps(sorted(grants), ensure_ascii=False))
    except Exception as exc:
        # 特权授予只是鉴权链路的**附加富化**：查库/缓存瞬时异常绝不阻断整体鉴权，也绝不
        # fail-open。退化为「无特权授予」（fail-closed：diag/运维等特权工具隐身），下次请求重试。
        log.warning(f'privileged grants lookup failed for {agent_hasn_id}, defaulting to none: {exc!r}')
        grants = set()

    # ENV bootstrap 每次现算合并（纯 settings 解析、无 IO：不进缓存、不受上面异常影响；
    # 改 ENV 重启即生效，不受 TTL 影响）
    return frozenset(grants | _env_privileged_grants(agent_hasn_id))


async def invalidate_privileged_grants_cache(agent_hasn_id: str) -> None:
    """清除 Agent 特权授予缓存（Admin 授予/撤销后调用，即时生效）。"""
    await redis_client.delete(f'privileged_grants:{agent_hasn_id}')


async def create_default_agent_scopes(db: AsyncSession, agent_hasn_id: str, owner_hasn_id: str) -> None:
    """
    为新创建的 Agent 插入默认权限配置

    :param db: 数据库会话
    :param agent_hasn_id: Agent 的 HASN ID
    :param owner_hasn_id: Owner 的 HASN ID
    :return:
    """
    from sqlalchemy import text

    # v3：判定只剩 default_mode + capability_modes（出厂全开 allow）；scopes/post_needs_review 列已 drop。
    await db.execute(
        text("""
            INSERT INTO hasn_agent_scopes
                (agent_hasn_id, owner_hasn_id, default_mode, capability_modes)
            VALUES
                (:agent_hasn_id, :owner_hasn_id, 'allow', '{}'::jsonb)
            ON CONFLICT (agent_hasn_id) DO NOTHING
        """),
        {
            'agent_hasn_id': agent_hasn_id,
            'owner_hasn_id': owner_hasn_id,
        },
    )
    await db.commit()


async def update_agent_modes(
    db: AsyncSession,
    agent_hasn_id: str,
    *,
    default_mode: str,
    capability_modes: dict,
) -> None:
    """更新 Agent 三态授权（D3）：写 default_mode/capability_modes，失效缓存；
    **不重签 JWT / 不吊销 key**（消费时活取，即时生效）。

    :param default_mode: allow|ask|deny（非法值落 allow）
    :param capability_modes: {capability_key: allow|ask|deny}
    """
    import json as _json

    from sqlalchemy import text

    if default_mode not in ('allow', 'ask', 'deny'):
        default_mode = 'allow'
    caps_json = _json.dumps(capability_modes or {}, ensure_ascii=False)

    # UPSERT（不是裸 UPDATE）：老 Agent 可能从未插入过 scopes 行（create_default_agent_scopes 在
    # 其创建之后才引入，或迁移期遗漏）。裸 UPDATE 在无行时影响 0 行、**静默丢弃**主人的权限更改
    # （含审批「总是允许」写回），且云端仍回 200 → 调用方误以为已保存（线上「总是允许写回成功但
    # hasn_agent_scopes 无记录」的根因）。owner_hasn_id 由 hasn_agents 现查派生，无需改调用签名；
    # Agent 必存在（service 层 _assert_owns 先校验），故 INSERT...SELECT 必命中。
    # 投影用 a.hasn_id 列本身（WHERE 已保证 = :agent_hasn_id），使 :agent_hasn_id 只出现一次——
    # 否则同名绑定既落 SELECT 投影位又落 WHERE，asyncpg 推不出类型（AmbiguousParameterError，生产同炸）。
    # :default_mode/:capability_modes 显式 CAST，确保 prepared 语句类型明确。
    await db.execute(
        text("""
            INSERT INTO hasn_agent_scopes (agent_hasn_id, owner_hasn_id, default_mode, capability_modes)
            SELECT a.hasn_id, a.owner_id, CAST(:default_mode AS varchar), CAST(:capability_modes AS jsonb)
            FROM hasn_agents a
            WHERE a.hasn_id = :agent_hasn_id
            ON CONFLICT (agent_hasn_id) DO UPDATE
                SET default_mode = EXCLUDED.default_mode,
                    capability_modes = EXCLUDED.capability_modes,
                    updated_time = NOW()
        """),
        {'agent_hasn_id': agent_hasn_id, 'default_mode': default_mode, 'capability_modes': caps_json},
    )
    await db.commit()

    # D3：失效缓存即可，下一次工具发现/执行现查即时生效；不重签 JWT、不吊销 key。
    await invalidate_agent_scopes_cache(agent_hasn_id)

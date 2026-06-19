"""
Agent JWT 认证模块

Agent 使用独立的 JWT 进行身份认证，与 Owner JWT 平级但权限受限。
每个 Agent JWT 包含 scopes 字段，用于细粒度权限控制。

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
from backend.core.conf import settings
from backend.database.redis import redis_client
from backend.utils.timezone import timezone

# 默认 Agent Scopes（统一 domain:action 冒号词表，P1 词表迁移）
# 注：三态授权落地后 default_mode='allow' 才是判定真相（消费时活取 D3）。
# v3 起 hasn_agent_scopes.scopes 列已 drop（16-doc D-v3-2）；本常量是 JWT
# scopes 审计 claim 的**唯一固定来源**（不再 per-agent 入库），不参与任何判定。
DEFAULT_AGENT_SCOPES = [
    'community:read',
    'community:comment',
    'community:interact',
    'community:circle',
    'community:doc',
    'message:read',
    'contact:read',
    'task:execute',
    'knowledge:read',
    'profile:read',
    # marketplace 工具集（15-技能市场/11-doc 权威源）。三态默认 allow，本数组仅审计快照。
    'marketplace:read',
    'marketplace:install',
    'marketplace:publish',
    # designsystem 自研设计系统应用（14-doc/20 设计 §7；DS-P7 铸 scope）。写类 import/save 落 :write；
    # 分享/发布（P9/P10）落 :publish。读类与确定性纯函数无 scope（不在此登记，避免假闸门）。
    # check_scopes 仍按此 claim 校验，故 Agent 调云端 designsystem/agent/* 写类必须在此铸入。
    'designsystem:write',
    'designsystem:publish',
    # film 视频生成应用（14-doc/18 设计；VC-P4 铸 scope）。读类 list/get/stage.artifact 落 :read；
    # 写类（建项目/各阶段生成/sandbox/pipeline/stage.intervene/continue）落 :write（出厂 Ask）；
    # 上传产物到云端 artifact.upload 落 :export。check_scopes 按此 claim 校验，Agent 调 film 本地工具
    # 经 daemon 三态闸门需这些 claim 在册。
    'film:read',
    'film:write',
    'film:export',
    # plan 规划与目标管理应用（19-doc 设计 §9.1；PLAN-P1 铸 scope）。分身经 hasn.plan.* 管理主人的
    # 目标/计划/待办/日程/习惯：读类 list/get/today 无 scope（确定性读，不设假闸门）；写类（建/改/删/
    # 排期/打卡/捕获/triage/decompose）落 :write（出厂 Allow）；排程（schedule/reschedule，Motion 自动
    # 排程建/删 flex 块）落 :schedule（出厂 Allow，独立 scope 便于 owner 单独管控，PLAN-P4b 铸）。委托
    # :delegate 随 P5 落地再铸。check_scopes 按此 claim 校验，Agent 调 /api/v1/plan/agent/* 写类必须在此铸入。
    'plan:read',
    'plan:write',
    'plan:schedule',
]


def normalize_scope(scope: str) -> str:
    """归一 scope 词表（过渡期兜底）：把点号 / 冒号统一成冒号。

    迁移窗口内防回归：``message.read`` 与 ``message:read`` 视为等价。
    # TODO(P5后): 全栈迁移完成后移除归一兜底，统一只认冒号。
    """
    return scope.replace('.', ':')


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
        scopes = payload.get('scopes', [])
        session_uuid = payload.get('session_uuid')
        expire = payload.get('exp')

        if not all([agent_hasn_id, owner_hasn_id, owner_user_id, session_uuid, expire]):
            raise errors.TokenError(msg='Agent Token 无效')

        return AgentTokenPayload(
            agent_hasn_id=agent_hasn_id,
            agent_name=agent_name or '',
            owner_hasn_id=owner_hasn_id,
            owner_user_id=int(owner_user_id),
            scopes=scopes,
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
    scopes: list[str],
) -> AgentAccessToken:
    """
    生成 Agent JWT token

    :param agent_hasn_id: Agent 的 HASN ID
    :param agent_name: Agent 显示名
    :param owner_hasn_id: Owner 的 HASN ID
    :param owner_user_id: Owner 的 user_id
    :param scopes: 权限列表
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
        'scopes': scopes,
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
        scopes=scopes,
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

    三态判定真相是 default_mode + capability_modes。``scopes`` 仅作 JWT 审计 claim 的
    固定占位（DEFAULT_AGENT_SCOPES，不再 per-agent 入库，16-doc D-v3-2）；
    ``post_needs_review`` 死字段已随表列一并移除。default_mode 缺失/非法→'allow'（默认全开）。
    """
    config.setdefault('scopes', DEFAULT_AGENT_SCOPES)
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

    v3（16-doc D-v3-2）起 ``scopes``/``post_needs_review`` 列已 drop：判定只看
    default_mode + capability_modes；``scopes`` 仅作 JWT 审计 claim 的固定占位
    （DEFAULT_AGENT_SCOPES，由 _ensure_policy_defaults 补齐）。

    :param db: 数据库会话
    :param agent_hasn_id: Agent 的 HASN ID
    :return: {"scopes": [...], "default_mode": str, "capability_modes": dict}
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
    获取 Agent 权限配置（带缓存，含三态字段）

    :param agent_hasn_id: Agent 的 HASN ID
    :param db: 数据库会话
    :return: {"scopes": [...], "post_needs_review": bool, "default_mode": str, "capability_modes": dict}
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

    await db.execute(
        text("""
            UPDATE hasn_agent_scopes
            SET default_mode = :default_mode,
                capability_modes = CAST(:capability_modes AS jsonb),
                updated_time = NOW()
            WHERE agent_hasn_id = :agent_hasn_id
        """),
        {'agent_hasn_id': agent_hasn_id, 'default_mode': default_mode, 'capability_modes': caps_json},
    )
    await db.commit()

    # D3：失效缓存即可，下一次工具发现/执行现查即时生效；不重签 JWT、不吊销 key。
    await invalidate_agent_scopes_cache(agent_hasn_id)

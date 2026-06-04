"""一次性能力票据（capability ticket）——H10 令牌重试 / doc15 §3.3 / A-P2。

owner 批准某条 ask 审批请求后，云端签一张**短时一次性** JWT 票据返回给 daemon；
hasn-mcp 带 `X-Capability-Ticket` 重试，云端验票（签名 + 未过期 + jti 未用 +
agent/tool/args_hash 与本次调用匹配）→ **跳过 ask 闸门直接执行**。

防重放：jti 经 Redis `SETNX capability_ticket:used:{jti}` 在**消费时原子认领**——首个消费者
得 1 放行，再次出现得 0 即重放拒绝。票据绑定 args_hash，换参 / 越权复用一律失败。
密钥复用 Agent JWT 同套 `settings.TOKEN_SECRET_KEY` / `TOKEN_ALGORITHM`（jose）。
"""

from __future__ import annotations

import logging
import uuid

from datetime import timedelta

from jose import JWTError, jwt

from backend.core.conf import settings
from backend.database.redis import redis_client
from backend.utils.timezone import timezone

logger = logging.getLogger(__name__)

TICKET_TYPE = 'capability_ticket'
# 一次性票据有效期（A-P2）：5 分钟，足够 daemon 换票→hasn-mcp 重试一次。
TICKET_TTL_SECONDS = 300
_USED_KEY = 'capability_ticket:used:{jti}'


def issue_capability_ticket(
    *, request_id: str, agent_hasn_id: str, owner_hasn_id: str, tool_name: str, args_hash: str
) -> tuple[str, str]:
    """签一张一次性能力票据，返回 (ticket_jwt, jti)。绑定 agent/tool/args_hash 防越权复用。"""
    jti = uuid.uuid4().hex
    exp = timezone.now() + timedelta(seconds=TICKET_TTL_SECONDS)
    payload = {
        'typ': TICKET_TYPE,
        'jti': jti,
        'request_id': request_id,
        'agent_hasn_id': agent_hasn_id,
        'owner_hasn_id': owner_hasn_id,
        'tool_name': tool_name,
        'args_hash': args_hash,
        'exp': timezone.to_utc(exp).timestamp(),
    }
    ticket = jwt.encode(payload, settings.TOKEN_SECRET_KEY, settings.TOKEN_ALGORITHM)
    return ticket, jti


async def consume_capability_ticket(
    ticket: str, *, agent_hasn_id: str, tool_name: str, args_hash: str
) -> dict | None:
    """验票 + **原子消费**。匹配本次调用且首次消费 → 返回 claims；否则 None（拒绝/重放）。

    校验链：签名 + 未过期（jose verify_exp）→ typ → agent/tool/args_hash 三项匹配 →
    Redis SETNX 认领 jti（首个得 1 放行；已存在得 0 = 重放/已用，拒绝）。
    """
    try:
        claims = jwt.decode(
            ticket,
            settings.TOKEN_SECRET_KEY,
            algorithms=[settings.TOKEN_ALGORITHM],
            options={'verify_exp': True},
        )
    except JWTError:
        return None

    if claims.get('typ') != TICKET_TYPE:
        return None
    if claims.get('agent_hasn_id') != agent_hasn_id:
        return None
    if claims.get('tool_name') != tool_name:
        return None
    if claims.get('args_hash') != args_hash:
        return None
    jti = claims.get('jti')
    if not jti:
        return None

    try:
        claimed = await redis_client.set(_USED_KEY.format(jti=jti), '1', nx=True, ex=TICKET_TTL_SECONDS)
    except Exception:
        logger.exception('capability ticket jti claim failed')
        return None
    if not claimed:
        return None  # 重放 / 已消费
    return claims

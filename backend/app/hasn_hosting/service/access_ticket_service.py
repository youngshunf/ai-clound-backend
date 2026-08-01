"""节点访问票据与会话授予（H4 · 契约 §3.4）。

浏览器要进托管容器里的 WebUI，全链路是三段一次性凭据，任何一段都不能重放：

```
① daemon → 云端 POST /app/cloud-nodes/{node_id}/access-ticket   → {ticket, expires_at, edge_url}
② 浏览器 GET https://<edge>/__hasn/enter?t=<ticket>
③ edge   → 云端 POST /internal/cloud-nodes/access-ticket/redeem → GETDEL 票据 + 签 grant
⑤ edge   → 容器 daemon POST /api/v1/session/from-grant {grant}
⑥ daemon → 云端 POST /node/cloud/session-grant/verify           → jti 一次性核销 + 回三元组
```

要点：

- **票据一次性**：Redis 值经 Lua `GET+DEL` 原子取走，TTL 60s；重放必定拿不到第二次。
- **票据 key 存 sha256(ticket)**：Redis 被读到也换不出明文票据。
- **grant 一次性**：`jti` 用 `SET NX` 核销，重放归一为 `session_grant_replayed`。
- **不信 edge**（D-18）：云端只回三元组，daemon 还要自校验 node_id / owner_hasn_id 才建会话。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import jwt

from backend.app.hasn_hosting.constants import (
    ACCESS_TICKET_REDIS_PREFIX,
    ACCESS_TICKET_TTL_SECONDS,
    GRANT_ALGORITHM,
    GRANT_AUDIENCE,
    GRANT_ISSUER,
    GRANT_JTI_REDIS_PREFIX,
    GRANT_JTI_TTL_SECONDS,
    GRANT_TTL_SECONDS,
    GRANT_TYP,
)
from backend.common.log import log
from backend.core.conf import settings
from backend.database.redis import redis_client
from backend.utils.timezone import timezone

#: Redis 原子取走并删除（一次性票据的唯一正确姿势；GET 后再 DEL 存在并发双取窗口）
_GETDEL_LUA = "local v = redis.call('GET', KEYS[1]) if v then redis.call('DEL', KEYS[1]) end return v"


@dataclass(frozen=True)
class IssuedTicket:
    """签发结果：`ticket` 明文只回给发起方（daemon → 桌面端 → 浏览器）。"""

    ticket: str
    expires_at: str
    edge_url: str


@dataclass(frozen=True)
class TicketPayload:
    """票据核销后取回的载荷（edge 据此拿 grant，绝不自行推断归属）。"""

    user_id: int
    owner_hasn_id: str
    node_id: str
    host: str


@dataclass(frozen=True)
class GrantClaims:
    """会话授予校验通过后的三元组（回给 daemon，daemon 还要自校验一遍）。"""

    node_id: str
    owner_hasn_id: str
    user_id: int


def _ticket_key(ticket: str) -> str:
    return f'{ACCESS_TICKET_REDIS_PREFIX}:{hashlib.sha256(ticket.encode("utf-8")).hexdigest()}'


class AccessTicketService:
    """票据签发 / 一次性核销 + grant 签发 / 一次性校验。"""

    @staticmethod
    async def issue_ticket(
        *,
        user_id: int,
        owner_hasn_id: str,
        node_id: str,
        host: str,
    ) -> IssuedTicket:
        """签发 60s 一次性节点访问票据（§3.4①）。"""
        ticket = secrets.token_urlsafe(32)
        payload = {
            'user_id': user_id,
            'owner_hasn_id': owner_hasn_id,
            'node_id': node_id,
            'host': host,
        }
        await redis_client.setex(
            _ticket_key(ticket),
            ACCESS_TICKET_TTL_SECONDS,
            json.dumps(payload, ensure_ascii=False),
        )
        expires_at = timezone.now() + timedelta(seconds=ACCESS_TICKET_TTL_SECONDS)
        return IssuedTicket(
            ticket=ticket,
            expires_at=expires_at.isoformat(),
            edge_url=(settings.HOSTING_EDGE_BASE_URL or '').rstrip('/'),
        )

    @staticmethod
    async def redeem_ticket(ticket: str) -> TicketPayload | None:
        """一次性核销票据（§3.4③）：命中即取走并删除；未命中 / 过期 / 已用 一律 None。"""
        raw = await redis_client.eval(_GETDEL_LUA, 1, _ticket_key(ticket))
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            # 值被写坏属不变量破坏：票据已被删掉且无法归还 → error
            log.error('[hosting] 节点访问票据载荷解析失败，票据已作废')
            return None
        if not isinstance(data, dict):
            log.error('[hosting] 节点访问票据载荷不是 JSON object，票据已作废')
            return None
        return TicketPayload(
            user_id=int(data.get('user_id') or 0),
            owner_hasn_id=str(data.get('owner_hasn_id') or ''),
            node_id=str(data.get('node_id') or ''),
            host=str(data.get('host') or ''),
        )

    @staticmethod
    def sign_grant(*, node_id: str, owner_hasn_id: str, user_id: int) -> str:
        """签发会话授予 JWT（HS256 / TOKEN_SECRET_KEY / 60s，§3.4③）。"""
        now = timezone.now()
        claims: dict[str, Any] = {
            'iss': GRANT_ISSUER,
            'aud': GRANT_AUDIENCE,
            'typ': GRANT_TYP,
            'node_id': node_id,
            'owner_hasn_id': owner_hasn_id,
            'user_id': user_id,
            'jti': str(uuid.uuid4()),
            'exp': int((now + timedelta(seconds=GRANT_TTL_SECONDS)).timestamp()),
        }
        return jwt.encode(claims, settings.TOKEN_SECRET_KEY, algorithm=GRANT_ALGORITHM)

    @staticmethod
    async def verify_grant(grant: str) -> tuple[GrantClaims | None, str | None]:
        """校验会话授予：签名 / exp / typ / jti 一次性（§3.4⑥）。

        返回 `(claims, None)` 或 `(None, error_code)`；`error_code` 取
        `session_grant_invalid` / `session_grant_replayed`。
        """
        from backend.app.hasn_hosting.constants import ERR_GRANT_INVALID, ERR_GRANT_REPLAYED

        try:
            payload = jwt.decode(
                grant,
                settings.TOKEN_SECRET_KEY,
                algorithms=[GRANT_ALGORITHM],
                audience=GRANT_AUDIENCE,
                issuer=GRANT_ISSUER,
                options={'require': ['exp', 'jti']},
            )
        except jwt.InvalidTokenError as exc:
            # 4xx 类可预期拒绝 → warn（不是不变量破坏）
            log.warning(f'[hosting] session grant 校验失败: {exc.__class__.__name__}')
            return None, ERR_GRANT_INVALID

        if payload.get('typ') != GRANT_TYP:
            log.warning('[hosting] session grant typ 不匹配，拒绝')
            return None, ERR_GRANT_INVALID

        node_id = str(payload.get('node_id') or '')
        owner_hasn_id = str(payload.get('owner_hasn_id') or '')
        user_id = int(payload.get('user_id') or 0)
        jti = str(payload.get('jti') or '')
        if not node_id or not owner_hasn_id or not user_id or not jti:
            log.warning('[hosting] session grant 缺必要 claim，拒绝')
            return None, ERR_GRANT_INVALID

        # jti 一次性：SET NX 成功=首次核销；失败=重放
        first_use = await redis_client.set(
            f'{GRANT_JTI_REDIS_PREFIX}:{jti}',
            '1',
            ex=GRANT_JTI_TTL_SECONDS,
            nx=True,
        )
        if not first_use:
            log.warning(f'[hosting] session grant 重放被拒: node_id={node_id}')
            return None, ERR_GRANT_REPLAYED

        return GrantClaims(node_id=node_id, owner_hasn_id=owner_hasn_id, user_id=user_id), None


access_ticket_service = AccessTicketService()

"""设备授权码服务（H2 · 契约 §2.1 / §3.2）。

授权码是「云端预分配的 `hasn_nodes` 行」与「容器里首次启动的 daemon」之间唯一的一次性凭证：

- **签发**：明文码只在内存里存在一瞬，经服务端直接交给 hosting-agent（再由 agent 写进容器 tmpfs
  文件注入，禁止 `-e`）；库里只留 `sha256(明文)` 十六进制。明文**绝不**回给客户端、绝不进日志。
- **兑换**：一条 `UPDATE ... WHERE status='pending' AND expires_at>now() RETURNING` 原子消费，
  影响 0 行即失败，再分别归因 `code_not_found` / `code_expired` / `code_consumed`。
  「先查后改」在并发下会把同一个码兑换两次（两个容器抢到同一台设备身份），故必须单条原子语句。
"""

from __future__ import annotations

import hashlib
import secrets

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update

from backend.app.hasn_hosting.constants import (
    AUTHORIZATION_CODE_TTL_SECONDS,
    CODE_STATUS_CONSUMED,
    CODE_STATUS_EXPIRED,
    CODE_STATUS_PENDING,
    CODE_STATUS_REVOKED,
    ERR_CODE_CONSUMED,
    ERR_CODE_EXPIRED,
    ERR_CODE_NOT_FOUND,
)
from backend.app.hasn_hosting.model import HasnNodeAuthorizationCodes
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def hash_code(plain_code: str) -> str:
    """授权码明文 → sha256 十六进制（库里与查询口径唯一）。"""
    return hashlib.sha256(plain_code.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class MintedCode:
    """新铸授权码：`plain_code` 只在进程内流向 hosting-agent，绝不出网到客户端。"""

    plain_code: str
    code_hash: str
    node_id: str
    purpose: str


@dataclass(frozen=True)
class ExchangeFailure:
    """兑换失败归因（三分支互斥；调用方据此回 4xx + 机器可判别 error code）。"""

    error: str
    message: str


class AuthorizationCodeService:
    """授权码铸造 / 作废 / 原子兑换。"""

    @staticmethod
    async def revoke_pending(db: AsyncSession, *, node_id: str) -> int:
        """把某节点上所有仍 `pending` 的码置 `revoked`（重铸新码前调用，保证同时只有一把有效钥匙）。"""
        result = await db.execute(
            update(HasnNodeAuthorizationCodes)
            .where(
                HasnNodeAuthorizationCodes.node_id == node_id,
                HasnNodeAuthorizationCodes.status == CODE_STATUS_PENDING,
            )
            .values(status=CODE_STATUS_REVOKED, updated_time=timezone.now())
            .returning(HasnNodeAuthorizationCodes.id)
        )
        return len(result.scalars().all())

    async def mint(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        owner_hasn_id: str,
        node_id: str,
        purpose: str,
    ) -> MintedCode:
        """铸一把新授权码（默认 5 分钟有效），同时作废该节点上的旧 pending 码。

        调用方负责在同一事务里 flush/commit。返回的 `plain_code` **只能**交给 hosting-agent。
        """
        await self.revoke_pending(db, node_id=node_id)

        plain_code = secrets.token_urlsafe(32)
        code_hash = hash_code(plain_code)
        row = HasnNodeAuthorizationCodes(
            code_hash=code_hash,
            user_id=user_id,
            owner_hasn_id=owner_hasn_id,
            node_id=node_id,
            purpose=purpose,
            expires_at=timezone.now() + timedelta(seconds=AUTHORIZATION_CODE_TTL_SECONDS),
            consumed_at=None,
            status=CODE_STATUS_PENDING,
        )
        db.add(row)
        await db.flush()
        return MintedCode(plain_code=plain_code, code_hash=code_hash, node_id=node_id, purpose=purpose)

    @staticmethod
    async def consume(
        db: AsyncSession,
        *,
        plain_code: str,
        node_id: str,
    ) -> tuple[HasnNodeAuthorizationCodes | None, ExchangeFailure | None]:
        """原子一次性消费授权码（契约 §3.2）。

        成功返回 `(row, None)`；失败返回 `(None, ExchangeFailure)`，错误码三分支互斥：

        - `code_not_found`：库里没有这个 code_hash，或它不属于该 node_id（不泄露归属，一律当不存在）
        - `code_expired`：已过期 / 已被作废
        - `code_consumed`：已经兑换过（同码二次兑换必须落这里）
        """
        code_hash = hash_code(plain_code)
        result = await db.execute(
            update(HasnNodeAuthorizationCodes)
            .where(
                HasnNodeAuthorizationCodes.code_hash == code_hash,
                HasnNodeAuthorizationCodes.node_id == node_id,
                HasnNodeAuthorizationCodes.status == CODE_STATUS_PENDING,
                HasnNodeAuthorizationCodes.expires_at > func.now(),
            )
            .values(status=CODE_STATUS_CONSUMED, consumed_at=func.now(), updated_time=func.now())
            .returning(HasnNodeAuthorizationCodes)
        )
        row = result.scalars().first()
        if row is not None:
            return row, None

        # 影响 0 行 → 回查归因。查询只按 code_hash（不带 node_id），以便把「码存在但节点对不上」
        # 与「码根本不存在」统一归到 code_not_found，避免探测他人节点。
        existing = (
            await db.execute(
                select(HasnNodeAuthorizationCodes).where(HasnNodeAuthorizationCodes.code_hash == code_hash)
            )
        ).scalar_one_or_none()
        if existing is None or existing.node_id != node_id:
            return None, ExchangeFailure(error=ERR_CODE_NOT_FOUND, message='授权码不存在')
        if existing.status == CODE_STATUS_CONSUMED:
            return None, ExchangeFailure(error=ERR_CODE_CONSUMED, message='授权码已被兑换')
        if existing.status in (CODE_STATUS_EXPIRED, CODE_STATUS_REVOKED):
            return None, ExchangeFailure(error=ERR_CODE_EXPIRED, message='授权码已失效')
        # status='pending' 但没命中 → 只可能是过期
        return None, ExchangeFailure(error=ERR_CODE_EXPIRED, message='授权码已过期')


authorization_code_service = AuthorizationCodeService()

"""M1 移动端 JWT 吊销表 ORM 模型 + CRUD 工具.

表 `jwt_revocations`:
- jti (VARCHAR(64), PRIMARY KEY)  — JWT 唯一标识; M1 直接复用 access_token 的
  session_uuid (UUID v4 字符串) 作为 jti
- user_id (BIGINT, not null, default 0) — 吊销对应的用户 ID, 便于"一键下线该用户所有 session"
- revoked_at (TIMESTAMPTZ, not null) — 吊销时间
- expires_at (TIMESTAMPTZ, nullable) — 原 JWT exp 时间 (便于后台批量清理过期吊销记录)

Alembic 迁移: backend/alembic/versions/20260421_b2_create_jwt_revocations.py
"""
from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone
from backend.utils.timezone import timezone


class JwtRevocation(Base):
    """JWT 吊销记录 (B2)."""

    __tablename__ = 'jwt_revocations'

    jti: Mapped[str] = mapped_column(
        sa.String(64), primary_key=True, comment='JWT 唯一标识 (M1 复用 session_uuid)'
    )
    user_id: Mapped[int] = mapped_column(
        sa.BigInteger(), nullable=False, default=0, comment='吊销对应的用户 ID'
    )
    revoked_at: Mapped[datetime] = mapped_column(
        TimeZone, nullable=False, default_factory=timezone.now, comment='吊销时间'
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        TimeZone, nullable=True, default=None, comment='原 JWT exp 时间 (便于清理)'
    )


async def revoke_jwt(
    db,
    *,
    jti: str,
    user_id: int,
    expires_at: datetime | None = None,
) -> None:
    """将 jti 写入 jwt_revocations.

    幂等: 同一 jti 重复 logout 不重复插入 (先查后插, 跨方言稳定)。
    """
    existing = await db.execute(
        sa.select(JwtRevocation.jti).where(JwtRevocation.jti == jti)
    )
    if existing.scalars().first() is not None:
        return

    row = JwtRevocation(
        jti=jti,
        user_id=user_id,
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()


async def is_jwt_revoked(db, *, jti: str) -> bool:
    """检查 jti 是否已被吊销."""
    result = await db.execute(
        sa.select(JwtRevocation.jti).where(JwtRevocation.jti == jti)
    )
    return result.scalars().first() is not None

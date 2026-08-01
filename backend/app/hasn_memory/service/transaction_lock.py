"""记忆合并使用的 PostgreSQL 事务级互斥锁。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def acquire_memory_transaction_lock(db: AsyncSession, lock_key: str) -> None:
    """锁定一个记忆资源直到当前事务结束，串行化重复 Celery 投递。"""
    await db.execute(
        text('SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))'),
        {'lock_key': lock_key},
    )

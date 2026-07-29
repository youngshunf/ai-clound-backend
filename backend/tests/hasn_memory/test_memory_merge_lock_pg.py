"""记忆合并事务锁的真实 PostgreSQL 验收。"""

from __future__ import annotations

import uuid

import pytest

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_memory.service.transaction_lock import acquire_memory_transaction_lock
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


async def test_memory_transaction_lock_serializes_same_resource() -> None:
    """同一资源键在首事务提交前不能被第二个事务获取。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    first = session_factory()
    second = session_factory()
    lock_key = f'memory-lock-e2e:{uuid.uuid4().hex}'
    try:
        try:
            async with engine.connect() as connection:
                await connection.execute(select(1))
        except Exception as exc:
            pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

        await acquire_memory_transaction_lock(first, lock_key)
        second_acquired = (
            await second.execute(
                text('SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_key, 0))'),
                {'lock_key': lock_key},
            )
        ).scalar_one()
        assert second_acquired is False

        await first.commit()
        second_acquired_after_commit = (
            await second.execute(
                text('SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_key, 0))'),
                {'lock_key': lock_key},
            )
        ).scalar_one()
        assert second_acquired_after_commit is True
    finally:
        await first.rollback()
        await second.rollback()
        await first.close()
        await second.close()
        await engine.dispose()

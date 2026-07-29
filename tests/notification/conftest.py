"""统一通知服务（app/notification）集成测试基础设施。

- 连真实本地 PostgreSQL（127.0.0.1:15432/huanxing），事务回滚隔离（零 Mock 零 Fake）。
- 复用社区 conftest 的 seed_human/seed_agent；偏好行用本地 helper 直插。
"""
from __future__ import annotations

import time

import pytest_asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.app.notification.service.notification_im_outbox import (
    build_notification_im_relay,
)

# 复用社区种子助手（同一套真库种子约定）
from tests.hasn_community.conftest import seed_agent as seed_agent
from tests.hasn_community.conftest import seed_human as seed_human

# 本地开发数据库；测试基础设施不读取仓库 `.env`，避免 worktree 错连默认 5432。
ASYNC_DATABASE_URL = 'postgresql+psycopg://mac@127.0.0.1:15432/huanxing'


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    """事务隔离的 AsyncSession（用例结束自动回滚）。"""
    engine = create_async_engine(ASYNC_DATABASE_URL, poolclass=NullPool)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(
        bind=conn,
        expire_on_commit=False,
        join_transaction_mode='create_savepoint',
    )
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def bind_real_im_gateway(db: AsyncSession, monkeypatch):
    """让通知卡片的 IM 写点复用同一真实数据库隔离事务。"""

    class _SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False

    class _SessionFactory:
        def __call__(self):
            return _SessionContext()

        def begin(self):
            return _SessionContext()

    session_factory = _SessionFactory()
    gateway = PythonLocalImGateway(session_factory=session_factory)
    monkeypatch.setattr(
        'backend.app.notification.service.notification_carrier.get_im_gateway',
        lambda: gateway,
    )
    yield gateway, session_factory


@pytest_asyncio.fixture
async def drain_notification_outbox(db: AsyncSession, bind_real_im_gateway):
    """真实领取并投递通知 outbox，返回单轮 relay 统计。"""
    gateway, session_factory = bind_real_im_gateway
    relay = build_notification_im_relay(
        session_factory=session_factory,
        gateway=gateway,
        instance_id='notification-test-relay',
    )

    async def _drain():
        stats = await relay.drain_once(
            now=int(time.time()) + 1,
            batch_limit=100,
        )
        await db.flush()
        return stats

    return _drain


async def seed_preference(
    db: AsyncSession,
    *,
    owner_id: str,
    category: str = '*',
    channels: dict | None = None,
    dnd: dict | None = None,
) -> None:
    """直插一条偏好行。"""
    import json

    await db.execute(
        text(
            'INSERT INTO hasn_notification_preferences (owner_id, category, channels, dnd, '
            'created_time, updated_time) VALUES (:owner, :cat, CAST(:ch AS jsonb), '
            'CAST(:dnd AS jsonb), now(), now())'
        ),
        {
            'owner': owner_id,
            'cat': category,
            'ch': json.dumps(channels or {}),
            'dnd': json.dumps(dnd or {}),
        },
    )
    await db.flush()


async def notification_outbox_result(
    db: AsyncSession,
    command_id: str,
) -> tuple[str, int | None]:
    """读取通知发送命令的终态与真实消息 ID。"""
    row = (
        await db.execute(
            text(
                'SELECT status, message_id '
                'FROM public.hasn_notification_im_command_outbox '
                'WHERE command_id = :command_id'
            ),
            {'command_id': command_id},
        )
    ).one()
    return str(row.status), int(row.message_id) if row.message_id is not None else None

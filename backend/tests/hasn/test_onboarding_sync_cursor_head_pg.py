"""B2② onboarding bootstrap 游标返回权威 feed 真实 head（真实 PG，零 mock）。

背景（hasn-node 实施/90 §2 B2「登录游标回零」）：onboarding ensure 历史上硬编码
``sync_cursor='owner:{hasn_id}:0'``，旧版 daemon 无条件镜像 → 每次登录本地游标被
重置、feed 从头重放，且登录只拉一页，积压超一页的新事件永远追不上。修复后
bootstrap 游标 = ``hasn_sync_events`` 该 owner 当前 MAX(revision)（空 feed 为 0），
仅供全新设备首登起步；daemon 侧配套 bootstrap-only（本地已有可解析游标一律不覆盖）。

本测试锁 ``SqlAlchemyOnboardingGateway.get_sync_feed_head`` 的真实 SQL 行为：
- 空 feed（全新 owner）→ 0；
- 经真实 ``_append_sync_event`` 推进 N 条后 → N；
- 与其他 owner 的事件互不串扰。

需要 export DATABASE_PORT=15432（指向本地开发 PG）；PG 不可达时跳过而非硬失败。
末尾清理本测试 owner 的行，不污染库。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service.hasn_onboarding_service import SqlAlchemyOnboardingGateway
from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def sessionmaker_pg():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:  # 本地未起开发 PG → 跳过而非硬失败
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过 B2② head 回归：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _append_events(sessionmaker: async_sessionmaker, owner_id: str, count: int) -> None:
    gw = SqlAlchemySyncGateway()
    async with sessionmaker() as session:
        for idx in range(count):
            await gw._append_sync_event(
                session,
                owner_id=owner_id,
                hasn_id=owner_id,
                event_type='message.received',
                aggregate_type='message',
                aggregate_id=f'b2_head_{idx}',
                payload={'idx': idx},
            )
        await session.commit()


async def test_get_sync_feed_head_tracks_real_feed_max_revision(sessionmaker_pg) -> None:
    owner_a = f'h_{uuid.uuid4()}'
    owner_b = f'h_{uuid.uuid4()}'
    gateway = SqlAlchemyOnboardingGateway()
    try:
        # 全新 owner：feed 为空 → head=0（全新账号首登游标从 0 起步，行为不变）。
        async with sessionmaker_pg() as session:
            assert await gateway.get_sync_feed_head(session, owner_id=owner_a) == 0

        # 真实推进 feed：owner_a 3 条、owner_b 1 条 → head 按 owner 隔离，各回各的 MAX。
        await _append_events(sessionmaker_pg, owner_a, 3)
        await _append_events(sessionmaker_pg, owner_b, 1)
        async with sessionmaker_pg() as session:
            assert await gateway.get_sync_feed_head(session, owner_id=owner_a) == 3
            assert await gateway.get_sync_feed_head(session, owner_id=owner_b) == 1

        # 再推进 → head 跟随（修复前 onboarding 恒回 0，重登即重放这 5 条）。
        await _append_events(sessionmaker_pg, owner_a, 2)
        async with sessionmaker_pg() as session:
            assert await gateway.get_sync_feed_head(session, owner_id=owner_a) == 5
    finally:
        async with sessionmaker_pg() as session:
            await session.execute(
                sa.text('DELETE FROM public.hasn_sync_events WHERE owner_id IN (:a, :b)'),
                {'a': owner_a, 'b': owner_b},
            )
            await session.commit()

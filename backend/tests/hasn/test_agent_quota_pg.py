"""分身数量权益门禁·真实 PostgreSQL 验收。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.model.user_subscription import UserSubscription
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service.hasn_agents_service import assert_agent_creation_allowed
from backend.app.hasn.service.hasn_auth import register_hasn_agent
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def sess() -> AsyncIterator[AsyncSession]:
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()
        await async_engine.dispose()


async def _seed_contract_and_agents(sess: AsyncSession, *, max_agents: int, agent_count: int) -> tuple[int, str]:
    suffix = uuid.uuid4().hex[:12]
    user_id = 8_000_000_000 + int(suffix[:8], 16)
    owner_id = f'h_quota_{suffix}'
    now = timezone.now()
    sess.add(
        HasnHumans(
            hasn_id=owner_id,
            star_id=f'q{suffix}',
            user_id=user_id,
            nickname=f'配额用户{suffix}',
            status='active',
        )
    )
    sess.add(
        UserSubscription(
            app_code='huanxing',
            user_id=user_id,
            tier='ultra' if max_agents == -1 else 'free',
            subscription_type='monthly',
            monthly_credits=Decimal(0),
            current_credits=Decimal(0),
            used_credits=Decimal(0),
            purchased_credits=Decimal(0),
            billing_cycle_start=now,
            billing_cycle_end=now,
            status='active',
            auto_renew=True,
            max_agents=max_agents,
            contract_no=f'HXQ{suffix}',
        )
    )
    for index in range(agent_count):
        sess.add(
            HasnAgents(
                hasn_id=f'a_quota_{suffix}_{index}',
                star_id=f'quota-{suffix}-{index}',
                owner_id=owner_id,
                display_name=f'配额分身{suffix}{index}',
                agent_name=f'quota_{index}',
                status='active',
            )
        )
    await sess.flush()
    return user_id, owner_id


async def test_agent_creation_is_rejected_when_contract_limit_reached(sess: AsyncSession) -> None:
    user_id, owner_id = await _seed_contract_and_agents(sess, max_agents=1, agent_count=1)

    with pytest.raises(errors.ForbiddenError) as caught:
        await assert_agent_creation_allowed(sess, owner_id=owner_id, user_id=user_id)

    assert caught.value.data == {
        'error_code': 'agent_quota_exceeded',
        'max_agents': 1,
        'current_agents': 1,
    }


async def test_agent_creation_allows_unlimited_contract(sess: AsyncSession) -> None:
    user_id, owner_id = await _seed_contract_and_agents(sess, max_agents=-1, agent_count=2)

    usage = await assert_agent_creation_allowed(sess, owner_id=owner_id, user_id=user_id)

    assert usage.max_agents == -1
    assert usage.current_agents == 2


async def test_register_agent_cannot_bypass_contract_limit(sess: AsyncSession) -> None:
    """公开 API、WS、引导和内置分身共用的注册入口必须执行配额门禁。"""
    _, owner_id = await _seed_contract_and_agents(sess, max_agents=1, agent_count=1)

    with pytest.raises(errors.ForbiddenError) as caught:
        await register_hasn_agent(
            sess,
            owner_hasn_id=owner_id,
            agent_name='quota_blocked',
            display_name='被拦截的分身',
        )

    assert caught.value.data['error_code'] == 'agent_quota_exceeded'

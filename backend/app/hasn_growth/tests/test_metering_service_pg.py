"""获客计量上报 M3-f 真实 PG 验收（零 mock，回滚）。

覆盖 G7 采集按量计积分钩子：unit=0(free 不上报)、unit>0(真实接平台 CreditService 计扣)、
积分不足只告警不阻断。单价经环境变量注入（config 而非 mock 业务）。需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from decimal import Decimal

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_growth.service.metering_service import growth_metering_service
from backend.app.user_tier.service.credit_service import credit_service
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def test_metering_free_when_unit_zero(session, monkeypatch) -> None:
    monkeypatch.delenv('GROWTH_CRAWL_CREDIT_UNIT', raising=False)  # 默认 0 = free
    uid = 930000 + int(uuid.uuid4().int % 9000)
    res = await growth_metering_service.report_crawl_usage(session, user_id=uid, job_id=1, success_count=5)
    assert res['reported'] is False and res['credits'] == 0.0 and res['count'] == 5


async def test_metering_no_count_no_report(session, monkeypatch) -> None:
    monkeypatch.setenv('GROWTH_CRAWL_CREDIT_UNIT', '2')
    uid = 931000 + int(uuid.uuid4().int % 9000)
    res = await growth_metering_service.report_crawl_usage(session, user_id=uid, job_id=2, success_count=0)
    assert res['reported'] is False and res['count'] == 0


async def test_metering_deducts_when_priced(session, monkeypatch) -> None:
    monkeypatch.setenv('GROWTH_CRAWL_CREDIT_UNIT', '2')  # 2 积分/条
    uid = 932000 + int(uuid.uuid4().int % 9000)
    # 平台侧充值 100 积分（接真实 CreditService，建订阅+余额）
    await credit_service.add_credits(session, user_id=uid, credits=Decimal('100'), reference_type='test_seed')
    before = await credit_service.get_total_available_credits(session, uid, 'huanxing')

    res = await growth_metering_service.report_crawl_usage(session, user_id=uid, job_id=3, success_count=4)
    assert res['reported'] is True and res['credits'] == 8.0  # 2 * 4

    after = await credit_service.get_total_available_credits(session, uid, 'huanxing')
    assert after == before - Decimal('8')


async def test_metering_insufficient_not_blocking(session, monkeypatch) -> None:
    monkeypatch.setenv('GROWTH_CRAWL_CREDIT_UNIT', '500')  # 单价极高，必然超出（含免费订阅额度）
    uid = 933000 + int(uuid.uuid4().int % 9000)
    await credit_service.add_credits(session, user_id=uid, credits=Decimal('10'), reference_type='test_seed')
    before = await credit_service.get_total_available_credits(session, uid, 'huanxing')
    res = await growth_metering_service.report_crawl_usage(session, user_id=uid, job_id=4, success_count=4)  # 需 2000
    assert res['reported'] is False and res['error'] == 'insufficient_credits'
    # 采集不被阻断（无异常抛出即证明）；余额未被扣
    after = await credit_service.get_total_available_credits(session, uid, 'huanxing')
    assert after == before

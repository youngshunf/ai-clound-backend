"""获客计量上报 M3-f 真实 PG 验收（零 mock，回滚）。

覆盖 G7 采集按量计积分钩子：unit=0 时 free 不上报；unit>0 但 NewAPI 计量接缝未接通时，
必须显式返回 ``metering_seam_not_wired``，不得调用已退役的云端余额原语伪装扣费成功。
单价经环境变量注入（config 而非 mock 业务）。需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_growth.service.metering_service import growth_metering_service
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
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
    res = await growth_metering_service.report_crawl_usage(session, user_id=930001, job_id=1, success_count=5)
    assert res['reported'] is False and res['credits'] == 0.0 and res['count'] == 5


async def test_metering_no_count_no_report(session, monkeypatch) -> None:
    monkeypatch.setenv('GROWTH_CRAWL_CREDIT_UNIT', '2')
    res = await growth_metering_service.report_crawl_usage(session, user_id=931001, job_id=2, success_count=0)
    assert res['reported'] is False and res['count'] == 0


async def test_metering_priced_usage_reports_missing_newapi_seam(session, monkeypatch) -> None:
    monkeypatch.setenv('GROWTH_CRAWL_CREDIT_UNIT', '2')  # 2 积分/条
    res = await growth_metering_service.report_crawl_usage(session, user_id=932001, job_id=3, success_count=4)
    assert res == {
        'reported': False,
        'credits': 8.0,
        'count': 4,
        'error': 'metering_seam_not_wired',
    }


async def test_metering_missing_seam_does_not_block_collection(session, monkeypatch) -> None:
    monkeypatch.setenv('GROWTH_CRAWL_CREDIT_UNIT', '500')
    res = await growth_metering_service.report_crawl_usage(session, user_id=933001, job_id=4, success_count=4)
    assert res['reported'] is False
    assert res['credits'] == 2000.0
    assert res['error'] == 'metering_seam_not_wired'

"""增长 URL 去重服务回归（零 mock，真实 PostgreSQL :15432）。"""

from __future__ import annotations

import uuid

import pytest

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_growth.model import CrawledUrl
from backend.app.hasn_growth.service.url_dedup_service import UrlDedupService
from backend.app.hasn_growth.service.url_normalize import normalize_url
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


async def _pg_reachable() -> bool:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception:
        return False
    else:
        return True
    finally:
        await engine.dispose()


async def test_recently_succeeded_url_is_skipped_and_counted() -> None:
    """近期成功 URL 必须按规范化结果跳过，并累计命中次数。"""
    if not await _pg_reachable():
        pytest.skip('本地 PostgreSQL :15432 不可达，跳过')

    marker = uuid.uuid4().hex[:8]
    source_url = f'https://www.example.com/leads/{marker}?utm_source=quality'
    repeated_url = f'https://example.com/leads/{marker}'
    normalized = normalize_url(source_url)
    assert normalized is not None
    _, url_hash, _ = normalized
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_maker() as db:
            service = UrlDedupService(db, job_id=920080, source_type='website')
            await service.register(source_url, outcome='succeeded', lead_yield=2)
            await db.commit()

        async with session_maker() as db:
            service = UrlDedupService(db, job_id=920081, source_type='website')
            assert await service.filter_unseen([repeated_url]) == []
            assert service.skipped == 1
            await db.commit()

        async with session_maker() as db:
            row = (await db.execute(select(CrawledUrl).where(CrawledUrl.url_hash == url_hash))).scalar_one()
            assert row.crawl_count == 1
            assert row.hit_count == 1
            assert row.lead_yield == 2
    finally:
        async with session_maker() as db:
            await db.execute(delete(CrawledUrl).where(CrawledUrl.url_hash == url_hash))
            await db.commit()
        await engine.dispose()

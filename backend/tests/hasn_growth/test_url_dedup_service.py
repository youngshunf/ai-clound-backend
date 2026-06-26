"""URL 级去重 service 真实 PG 测试（零 mock，打真实 hasn_growth.crawled_url 表；阶段一 ②）。

事实源: docs/AI自动获客任务系统/08-采集引擎v3选型决策与众包线索池架构.md §4.4。
直接 register/filter_unseen/bump_lead_yield 真实写库 → flush（不 commit）→ 断言 → rollback。
每个用例用 uuid 化 URL 互不干扰；fixture 收尾 rollback，不留痕。
"""

from __future__ import annotations

import uuid

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_growth.model import CrawledUrl
from backend.app.hasn_growth.service.url_dedup_service import UrlDedupService
from backend.app.hasn_growth.service.url_normalize import normalize_url
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
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


def _unique_url() -> str:
    return f'https://example.com/lead/{uuid.uuid4().hex}'


def _hash(url: str) -> str:
    normalized = normalize_url(url)
    assert normalized is not None
    return normalized[1]


async def _fetch(session: AsyncSession, url: str) -> CrawledUrl | None:
    stmt = select(CrawledUrl).where(CrawledUrl.url_hash == _hash(url))
    return (await session.execute(stmt)).scalar_one_or_none()


async def test_register_inserts_row(session: AsyncSession) -> None:
    url = _unique_url()
    await UrlDedupService(session, job_id=1, source_type='public_web').register(url, outcome='succeeded')
    await session.flush()
    row = await _fetch(session, url)
    assert row is not None
    assert row.crawl_count == 1
    assert row.hit_count == 0
    assert row.lead_yield == 0
    assert row.last_outcome == 'succeeded'
    assert row.domain == 'example.com'


async def test_register_conflict_accumulates_crawl_count(session: AsyncSession) -> None:
    url = _unique_url()
    dedup = UrlDedupService(session, job_id=1, source_type='public_web')
    await dedup.register(url, outcome='succeeded')
    await session.flush()
    await dedup.register(url, outcome='succeeded')
    await session.flush()
    row = await _fetch(session, url)
    assert row is not None
    assert row.crawl_count == 2  # on_conflict 累加而非新增行


async def test_filter_unseen_skips_recently_succeeded(session: AsyncSession) -> None:
    url = _unique_url()
    dedup = UrlDedupService(session, job_id=1, source_type='public_web')
    await dedup.register(url, outcome='succeeded')
    await session.flush()
    kept = await dedup.filter_unseen([url])
    await session.flush()
    assert kept == []  # 近期成功过 → 被去重跳过
    assert dedup.skipped == 1
    row = await _fetch(session, url)
    assert row is not None
    assert row.hit_count == 1  # 命中计数 ++


async def test_filter_unseen_keeps_new_url(session: AsyncSession) -> None:
    url = _unique_url()
    kept = await UrlDedupService(session, job_id=1, source_type='public_web').filter_unseen([url])
    assert kept == [url]  # 库里没有 → 放行抓取


async def test_filter_unseen_keeps_non_succeeded_outcome(session: AsyncSession) -> None:
    url = _unique_url()
    dedup = UrlDedupService(session, job_id=1, source_type='public_web')
    await dedup.register(url, outcome='empty')  # 上次抓到空页 → 允许重抓
    await session.flush()
    kept = await dedup.filter_unseen([url])
    assert kept == [url]
    assert dedup.skipped == 0


async def test_filter_unseen_keeps_expired_succeeded(session: AsyncSession) -> None:
    url = _unique_url()
    dedup = UrlDedupService(session, job_id=1, source_type='public_web', window_days=30)
    await dedup.register(url, outcome='succeeded')
    await session.flush()
    # 回拨到窗口外（60 天前）→ 站点内容可能更新，允许重抓
    backdated = datetime.now(UTC) - timedelta(days=60)
    await session.execute(
        sa.update(CrawledUrl).where(CrawledUrl.url_hash == _hash(url)).values(last_crawled_at=backdated)
    )
    await session.flush()
    kept = await dedup.filter_unseen([url])
    assert kept == [url]


async def test_bump_lead_yield(session: AsyncSession) -> None:
    url = _unique_url()
    dedup = UrlDedupService(session, job_id=1, source_type='public_web')
    await dedup.register(url, outcome='succeeded')
    await session.flush()
    await dedup.bump_lead_yield(url, delta=2)
    await session.flush()
    row = await _fetch(session, url)
    assert row is not None
    assert row.lead_yield == 2


async def test_unparseable_url_kept_and_register_noop(session: AsyncSession) -> None:
    dedup = UrlDedupService(session, job_id=1, source_type='public_web')
    assert await dedup.filter_unseen(['']) == ['']  # 无法规范化 → 放行交给 provider
    count_stmt = select(sa.func.count()).select_from(CrawledUrl)
    before = (await session.execute(count_stmt)).scalar()
    await dedup.register('', outcome='succeeded')  # 不应插入任何行
    await session.flush()
    after = (await session.execute(count_stmt)).scalar()
    assert after == before

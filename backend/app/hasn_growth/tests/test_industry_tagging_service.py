"""行业标准化打标验证（规则纯函数 + 真实 PG normalize；零 mock，doc08 §4.3·阶段二 2.2）。

纯函数 ``match_industry_rule`` 不依赖 DB；``IndustryTaggingService.normalize`` 查真实 seed 的
``industry_tag`` 字典表（004 SQL 已 seed 35 行）。需 export DATABASE_PORT=15432。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_growth.service.industry_tagging_service import (
    IndustryTaggingService,
    match_industry_rule,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

_TAGS = [
    {'code': 'led_display', 'name': 'LED显示屏', 'aliases': ['LED屏', '显示屏', 'led']},
    {'code': 'catering', 'name': '餐饮', 'aliases': ['餐厅', '饭店']},
    {'code': 'education', 'name': '教育培训', 'aliases': ['培训', '教育']},
]


# ---------------- 规则匹配纯函数（无需 DB） ----------------


def test_rule_match_by_name() -> None:
    assert match_industry_rule(_TAGS, raw_industry='餐饮') == 'catering'


def test_rule_match_by_alias() -> None:
    assert match_industry_rule(_TAGS, raw_industry='LED屏批发') == 'led_display'


def test_rule_longest_match_wins() -> None:
    # 'LED显示屏'(name) 比 'led'(alias) 长 → 取 led_display，避免短词误命中
    assert match_industry_rule(_TAGS, raw_industry='我们是做LED显示屏的厂家') == 'led_display'


def test_rule_match_from_company_name() -> None:
    assert match_industry_rule(_TAGS, company_name='广州海珠区某某餐厅') == 'catering'


def test_rule_no_match_returns_none() -> None:
    assert match_industry_rule(_TAGS, raw_industry='区块链元宇宙Web3') is None


def test_rule_empty_input_returns_none() -> None:
    assert match_industry_rule(_TAGS, raw_industry=None, company_name='   ') is None


# ---------------- 真实 PG normalize（查 seed 字典，enable_llm=False 纯规则） ----------------


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    s = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield s
    finally:
        await s.rollback()
        await s.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_normalize_alias_hits_seed_dict(session: AsyncSession) -> None:
    svc = IndustryTaggingService(session, enable_llm=False)
    assert await svc.normalize(raw_industry='LED屏') == 'led_display'


@pytest.mark.asyncio
async def test_normalize_from_company_name(session: AsyncSession) -> None:
    svc = IndustryTaggingService(session, enable_llm=False)
    assert await svc.normalize(company_name='杭州某某教育培训机构') == 'education'


@pytest.mark.asyncio
async def test_normalize_no_match_returns_none(session: AsyncSession) -> None:
    svc = IndustryTaggingService(session, enable_llm=False)
    assert await svc.normalize(raw_industry='完全不存在的行业xyz123') is None


@pytest.mark.asyncio
async def test_normalize_caches_tags(session: AsyncSession) -> None:
    svc = IndustryTaggingService(session, enable_llm=False)
    tags1 = await svc.load_tags()
    tags2 = await svc.load_tags()
    assert tags1 is tags2  # 实例级缓存
    assert len(tags1) >= 35  # 004 seed 至少 35 个行业


@pytest.mark.asyncio
async def test_normalize_disabled_llm_no_network(session: AsyncSession) -> None:
    # enable_llm=False：规则不中即返回 None，绝不触网（确保纯规则路径可离线测）
    svc = IndustryTaggingService(session, enable_llm=False)
    assert await svc.normalize(raw_industry='xyz无法识别', company_name='abc公司') is None

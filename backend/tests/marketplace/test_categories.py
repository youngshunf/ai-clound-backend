"""统一分类选择器数据源 `get_marketplace_categories` 真实 PG 测试（零 mock）。

验证 SP-UI 后续：open `/categories` 返回**权威分类目录**——含中文显示名(name)+emoji(icon)
+已发布技能/模板计数，供 webui 技能/技能包/分身模板三页的统一分类选择器显示中文名。
需要 export DATABASE_PORT=15432（本地 PG 不可达则 skip）。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.marketplace.model import MarketplaceSkill
from backend.app.marketplace.service.search_service import search_service
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
    s = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield s
    finally:
        await s.rollback()
        await s.close()
        await engine.dispose()


async def test_categories_return_name_icon_and_counts(session):
    slug = f'cat{uuid.uuid4().hex[:10]}'
    cname = '测试分类显示名'
    await session.execute(
        text(
            'INSERT INTO hasn_marketplace.marketplace_category (slug, name, icon, sort_order, created_time, updated_time) '
            'VALUES (:s, :n, :i, 999, now(), now())'
        ),
        {'s': slug, 'n': cname, 'i': '🧪'},
    )
    skill_id = f'{slug}/probe'
    await session.execute(delete(MarketplaceSkill).where(MarketplaceSkill.skill_id == skill_id))
    session.add(
        MarketplaceSkill(
            skill_id=skill_id,
            namespace=slug,
            slug='probe',
            name='probe',
            category=slug,
            status='published',
            visibility='public',
        )
    )
    await session.flush()

    items = await search_service.get_marketplace_categories(session)
    by_slug = {item['category']: item for item in items}

    assert slug in by_slug, '权威目录里的分类应出现在选择器数据源中'
    item = by_slug[slug]
    assert item['name'] == cname, '应返回目录表中文显示名，而非 slug'
    assert item['icon'] == '🧪'
    assert item['skill_count'] >= 1
    assert 'template_count' in item


async def test_defined_category_listed_even_without_content(session):
    """权威目录里的分类即便暂无已发布内容也应列出（使创建可任选分类）。"""
    slug = f'empty{uuid.uuid4().hex[:10]}'
    await session.execute(
        text(
            'INSERT INTO hasn_marketplace.marketplace_category (slug, name, icon, sort_order, created_time, updated_time) '
            'VALUES (:s, :n, NULL, 998, now(), now())'
        ),
        {'s': slug, 'n': '空分类'},
    )
    await session.flush()

    items = await search_service.get_marketplace_categories(session)
    by_slug = {item['category']: item for item in items}
    assert slug in by_slug
    assert by_slug[slug]['name'] == '空分类'
    assert by_slug[slug]['skill_count'] == 0
    assert by_slug[slug]['template_count'] == 0

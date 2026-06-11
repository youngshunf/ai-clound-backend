"""deck_service.list_style_profiles 真实 DB 测试（零 mock）：builtin∪owner 可见 + owner 隔离。

SP-2：37 个 builtin（source='builtin'/owner_id='system'）对**任意 owner** 可见；owner 自定义只对
自己可见，**绝不**泄露其它 owner 的 custom 样式。用真实本地 PostgreSQL（不可达则 skip），
插入隔离测试行 → flush（不 commit）→ 断言 → rollback。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.deck.model import StyleProfile
from backend.app.deck.service.deck_service import BUILTIN_OWNER, deck_service
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


async def test_list_style_profiles_unions_builtin_and_isolates_owner(session):
    tag = uuid.uuid4().hex[:8]
    owner_a = f'h_owner_a_{tag}'
    owner_b = f'h_owner_b_{tag}'
    slug_builtin = f'zzz-builtin-{tag}'
    slug_a = f'zzz-custom-a-{tag}'
    slug_b = f'zzz-custom-b-{tag}'

    session.add_all([
        StyleProfile(
            slug=slug_builtin, label='测试内置', description='', source='builtin',
            design_contract={'palette': ['#000000']}, style_prompt='内置提示词',
            owner_id=BUILTIN_OWNER, rev=1,
        ),
        StyleProfile(
            slug=slug_a, label='A 的自定义', description='', source='custom',
            design_contract=None, style_prompt=None, owner_id=owner_a, rev=1,
        ),
        StyleProfile(
            slug=slug_b, label='B 的自定义', description='', source='custom',
            design_contract=None, style_prompt=None, owner_id=owner_b, rev=1,
        ),
    ])
    await session.flush()

    result = await deck_service.list_style_profiles(session, owner_id=owner_a)
    slugs = {item['slug'] for item in result['items']}

    # builtin 对 owner_a 可见
    assert slug_builtin in slugs
    # owner_a 自己的 custom 可见
    assert slug_a in slugs
    # owner_b 的 custom 绝不泄露给 owner_a（owner 隔离硬边界）
    assert slug_b not in slugs

    # 既有 37 个 seed builtin 也应在内（builtin 全局可见）
    builtin_items = [i for i in result['items'] if i['source'] == 'builtin']
    assert len(builtin_items) >= 1
    # builtin 排在前（order_by source==builtin desc）
    assert result['items'][0]['source'] == 'builtin'

    # 序列化字段齐全
    sample = next(i for i in result['items'] if i['slug'] == slug_builtin)
    assert sample['style_prompt'] == '内置提示词'
    assert sample['design_contract'] == {'palette': ['#000000']}


async def test_list_style_profiles_owner_b_does_not_see_owner_a_custom(session):
    tag = uuid.uuid4().hex[:8]
    owner_a = f'h_owner_a_{tag}'
    owner_b = f'h_owner_b_{tag}'
    slug_a = f'zzz-custom-a-{tag}'

    session.add(
        StyleProfile(
            slug=slug_a, label='A 的自定义', description='', source='custom',
            design_contract=None, style_prompt=None, owner_id=owner_a, rev=1,
        )
    )
    await session.flush()

    result = await deck_service.list_style_profiles(session, owner_id=owner_b)
    slugs = {item['slug'] for item in result['items']}
    assert slug_a not in slugs  # 对称：A 的 custom 对 B 不可见

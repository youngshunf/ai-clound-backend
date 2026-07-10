"""SKILLNAME-2：sync_agents 注入的 skill_display 反查——真实 PG 验收（零 mock）。

hasn_agents.skills 只存 skill_id slug 清单（无友好名/描述），命令浮层要显示真名+描述
需从 marketplace/个人技能目录反查。本测试锁住 _resolve_skill_display 的三条不变量：
- 市场技能按 skill_id 命中，中文名/描述优先（name_zh || name_en || name）。
- 个人技能 owner 内 scope（hasn_id），按 slug 或 personal_skill_id 命中。
- 查不到的 skill_id 不进 map（daemon 侧 humanize slug 兜底）；他人个人技能不泄漏。

事务末尾回滚，不留脏数据（仅 flush，不 commit）。需要本地 PostgreSQL :15432。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service.hasn_agents_service import _resolve_skill_display
from backend.app.marketplace.model.marketplace_personal_skill import MarketplacePersonalSkill
from backend.app.marketplace.model.marketplace_skill import MarketplaceSkill
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
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


async def test_resolve_skill_display_marketplace_and_personal(session: AsyncSession) -> None:
    """市场技能中文名优先 + 个人技能按 slug/id 命中 + 未命中不进 map + owner 隔离。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    other_owner = f'h_other_{tag}'
    # 个人技能唯一约束是 (user_id, slug)——两条测试行必须用不同 user_id 才能同 slug 共存；
    # 取大整数避开真实用户 ID 段，防污染共享 dev 库。
    base = int(tag, 16)
    owner_uid = 900_000_000 + (base % 1_000_000)
    other_uid = owner_uid + 1

    mkt_id = f'test/mkt/{tag}'
    mkt_zh_only = f'test/mkt/zh-{tag}'
    session.add(
        MarketplaceSkill(
            skill_id=mkt_id,
            name='Code Review',
            name_zh='代码审查',
            name_en='Code Review EN',
            description_zh='审查代码质量',
            description_en='review code quality',
        )
    )
    # 只有英文名/描述时应回退到 name_en/description_en。
    session.add(
        MarketplaceSkill(
            skill_id=mkt_zh_only,
            name='Fallback',
            name_en='English Only',
            description_en='english desc',
        )
    )
    # 个人技能：owner 内可命中（按 slug 或 personal_skill_id）。
    personal_id = f'psk_{tag}'
    personal_slug = f'my-writer-{tag}'
    session.add(
        MarketplacePersonalSkill(
            personal_skill_id=personal_id,
            user_id=owner_uid,
            hasn_id=owner,
            slug=personal_slug,
            name='写作助手',
            description='帮你把初稿写好',
        )
    )
    # 他人的个人技能：即便同 slug 也不得泄漏给本 owner（user_id 不同以满足唯一约束）。
    session.add(
        MarketplacePersonalSkill(
            personal_skill_id=f'psk_other_{tag}',
            user_id=other_uid,
            hasn_id=other_owner,
            slug=personal_slug,
            name='别人的技能',
            description='不该出现',
        )
    )
    await session.flush()

    result = await _resolve_skill_display(
        session,
        owner,
        [mkt_id, mkt_zh_only, personal_slug, personal_id, f'nonexistent/{tag}'],
    )

    # 市场技能中文名/描述优先。
    assert result[mkt_id] == {'name': '代码审查', 'description': '审查代码质量'}
    # 无中文时回退英文。
    assert result[mkt_zh_only] == {'name': 'English Only', 'description': 'english desc'}
    # 个人技能按 slug 与 personal_skill_id 两个键都补上（skills 里两种引用都兼容）。
    assert result[personal_slug] == {'name': '写作助手', 'description': '帮你把初稿写好'}
    assert result[personal_id] == {'name': '写作助手', 'description': '帮你把初稿写好'}
    # 未命中的 skill_id 不进 map（交给 daemon humanize）。
    assert f'nonexistent/{tag}' not in result
    # owner 隔离：他人个人技能的内容绝不出现。
    assert all(entry['name'] != '别人的技能' for entry in result.values())


async def test_resolve_skill_display_empty_input(session: AsyncSession) -> None:
    """空/纯空串输入直接返回空 map，不打库。"""
    assert await _resolve_skill_display(session, 'h_x', []) == {}
    assert await _resolve_skill_display(session, 'h_x', ['', '  ']) == {}

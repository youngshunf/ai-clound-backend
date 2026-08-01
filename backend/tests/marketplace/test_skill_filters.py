"""公共技能/精确批量解析 过滤器 真实测试（分身技能页元数据解析 + 公共技能区）。

背景：分身技能页此前只 `useMarketplaceSkills({page_size:100})` 拉前 100 条目录（共上万条）
来按 name 命中元数据 → 本分身真实已装的技能（finance/business-plan 等）命不中 → 卡片只露 skill_id。
修复：`/marketplace/open/skills` 新增两过滤：
  - `skill_ids`（逗号分隔）：只返回这些 skill_id，分身把已装 skill_id 全集传入一次拉全元数据；
  - `is_common`：只取公共技能（首登默认叠加、技能页「公共技能」区拉取）。
并在 `_format_skill` 输出 `is_common`，供前端区分公共/普通技能。

需要 export DATABASE_PORT=15432（本地 PG 不可达则跳过）。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.marketplace.model.marketplace_skill import MarketplaceSkill
from backend.app.marketplace.model.marketplace_skill_version import MarketplaceSkillVersion
from backend.app.marketplace.service.search_service import search_service
from backend.database.db import SQLALCHEMY_DATABASE_URL


@pytest_asyncio.fixture
async def db_session():
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


async def _seed_skill(session, skill_id, namespace, slug, *, is_common=False, **cols) -> None:
    await session.execute(delete(MarketplaceSkill).where(MarketplaceSkill.skill_id == skill_id))
    session.add(
        MarketplaceSkill(
            skill_id=skill_id, namespace=namespace, slug=slug,
            name=cols.pop('name', slug), status='published', visibility='public',
            is_common=is_common, **cols,
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_skill_ids_filter_returns_only_requested(db_session) -> None:
    """传 skill_ids 精确批量解析：只返回这些 id，其它发布中的技能不混入。"""
    tag = uuid.uuid4().hex[:8]
    ns = f'huanxing/finance-{tag}'
    want_a = f'{ns}/business-plan'
    want_b = f'{ns}/cashflow'
    noise = f'{ns}/unrelated'
    await _seed_skill(db_session, want_a, ns, 'business-plan', name='商业计划书')
    await _seed_skill(db_session, want_b, ns, 'cashflow', name='现金流')
    await _seed_skill(db_session, noise, ns, 'unrelated', name='无关技能')

    result = await search_service.search_skills(
        db_session, skill_ids=[want_a, want_b], page_size=100,
    )
    ids = {item['skill_id'] for item in result['items']}
    assert want_a in ids and want_b in ids
    assert noise not in ids                 # 未请求的不混入
    # 元数据真补全：名称非 skill_id（前端不再降级显示原始 id）
    by_id = {item['skill_id']: item for item in result['items']}
    assert by_id[want_a]['name'] == '商业计划书'
    assert by_id[want_b]['name'] == '现金流'


@pytest.mark.asyncio
async def test_skill_ids_empty_subset_when_none_match(db_session) -> None:
    """请求的 skill_id 全不在库（市场暂未收录/离线）→ 诚实返回空，不伪造。"""
    tag = uuid.uuid4().hex[:8]
    result = await search_service.search_skills(
        db_session, skill_ids=[f'no/such-{tag}'], page_size=100,
    )
    assert result['items'] == []
    assert result['total'] == 0


@pytest.mark.asyncio
async def test_is_common_filter_partitions_common_and_normal(db_session) -> None:
    """is_common=True 只取公共技能；is_common=False 只取非公共；省略则不过滤。"""
    tag = uuid.uuid4().hex[:8]
    ns = f'huanxing/common-{tag}'
    common_id = f'{ns}/onboarding'
    normal_id = f'{ns}/specialist'
    await _seed_skill(db_session, common_id, ns, 'onboarding', name='通用入门', is_common=True)
    await _seed_skill(db_session, normal_id, ns, 'specialist', name='专项技能', is_common=False)

    only_common = await search_service.search_skills(
        db_session, is_common=True, namespace=ns, page_size=100,
    )
    common_ids = {item['skill_id'] for item in only_common['items']}
    assert common_id in common_ids
    assert normal_id not in common_ids

    only_normal = await search_service.search_skills(
        db_session, is_common=False, namespace=ns, page_size=100,
    )
    normal_ids = {item['skill_id'] for item in only_normal['items']}
    assert normal_id in normal_ids
    assert common_id not in normal_ids


@pytest.mark.asyncio
async def test_format_skill_emits_is_common_flag(db_session) -> None:
    """_format_skill 输出 is_common，前端据此区分公共/普通技能。"""
    tag = uuid.uuid4().hex[:8]
    ns = f'huanxing/flag-{tag}'
    common_id = f'{ns}/c'
    await _seed_skill(db_session, common_id, ns, 'c', name='公共', is_common=True)

    result = await search_service.search_skills(
        db_session, skill_ids=[common_id], page_size=100,
    )
    assert result['items'], '应能解析到刚 seed 的公共技能'
    item = result['items'][0]
    assert 'is_common' in item
    assert item['is_common'] is True


@pytest.mark.asyncio
async def test_search_skill_emits_latest_immutable_snapshot(db_session) -> None:
    """公开搜索返回 latest 的真实版本与内容指纹，供存量 requirement 冻结。"""
    tag = uuid.uuid4().hex[:8]
    namespace = f'huanxing/snapshot-{tag}'
    skill_id = f'{namespace}/briefing'
    await _seed_skill(db_session, skill_id, namespace, 'briefing', name='简报')
    content_hash = 'a' * 64
    db_session.add(
        MarketplaceSkillVersion(
            skill_id=skill_id,
            version='1.2.3',
            content_hash=content_hash,
            is_latest=True,
        )
    )
    await db_session.flush()

    result = await search_service.search_skills(
        db_session,
        skill_ids=[skill_id],
        page_size=100,
    )

    assert len(result['items']) == 1
    assert result['items'][0]['latest_version'] == '1.2.3'
    assert result['items'][0]['content_hash'] == content_hash

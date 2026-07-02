"""公共技能 revision 抖动根治测试（doc11 §5.5，真实 PG 零 mock）。

覆盖：
  1. 快照查询确定性：同 skill 多版本行（is_latest 各异）下，`get_common_skill_snapshot`
     恒取 is_latest 行的指纹，多次调用 revision 稳定（不再依赖 PostgreSQL 返回顺序）。
  2. `github_sync._sync_skill_version` 写新版本前重置同 skill 旧行 is_latest（doc11 §5.5-2）：
     连写两个版本后只剩一条 is_latest=true，且快照指纹跟到新版本。
  3. bundle 侧对称（评审 D3）：`_common_bundle_fingerprints` 恒取 is_latest 版本行指纹，
     revision 稳定。
  4. partial unique index 兜底（本地库需已执行
     backend/sql/marketplace/migrations/2026-07-02-skill-version-latest-unique.sql）：
     同 skill 第二条 is_latest=true 行被写入期拦截。

所有 seed 走同一 session、从不 commit，末尾 rollback 不污染共享本地库。
需要 export DATABASE_PORT=15432（本地 PG）。
"""

from __future__ import annotations

import uuid

from decimal import Decimal

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.marketplace.model import MarketplaceSkill, MarketplaceSkillVersion
from backend.app.marketplace.model.marketplace_template import MarketplaceTemplate
from backend.app.marketplace.model.marketplace_template_version import MarketplaceTemplateVersion
from backend.app.marketplace.service.common_skills_service import (
    _common_bundle_fingerprints,
    get_common_skill_snapshot,
    get_skills_content_fingerprints,
)
from backend.app.marketplace.service.github_sync_service import github_sync_service
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_SNAPSHOT_REPEAT = 10  # revision 稳定性重复取样次数


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


def _tag() -> str:
    return uuid.uuid4().hex[:8]


async def _seed_common_skill(session, skill_id: str) -> MarketplaceSkill:
    namespace, slug = skill_id.rsplit('/', 1)
    skill = MarketplaceSkill(
        skill_id=skill_id,
        namespace=namespace,
        slug=slug,
        name=slug,
        status='published',
        visibility='public',
        is_common=True,
    )
    session.add(skill)
    await session.flush()
    return skill


def _version_row(skill_id: str, version: str, content_hash: str, *, is_latest: bool) -> MarketplaceSkillVersion:
    return MarketplaceSkillVersion(
        skill_id=skill_id,
        version=version,
        content_hash=content_hash,
        is_latest=is_latest,
    )


async def test_snapshot_picks_latest_row_and_revision_is_stable(session) -> None:
    """多版本行（is_latest 各异）下快照恒取 is_latest 行指纹，revision 多次取样稳定。"""
    skill_id = f'huanxing/test/rev-stable-{_tag()}'
    await _seed_common_skill(session, skill_id)
    session.add_all(
        [
            _version_row(skill_id, '1.0.0', 'fp-old-a', is_latest=False),
            _version_row(skill_id, '1.1.0', 'fp-current', is_latest=True),
            _version_row(skill_id, '0.9.0', 'fp-old-b', is_latest=False),
        ]
    )
    await session.flush()

    ids, revision = await get_common_skill_snapshot(session)
    assert skill_id in ids

    fingerprints = await get_skills_content_fingerprints(session, [skill_id])
    assert fingerprints[skill_id] == 'fp-current'

    for _ in range(_SNAPSHOT_REPEAT):
        again_ids, again_rev = await get_common_skill_snapshot(session)
        assert again_ids == ids
        assert again_rev == revision


async def test_github_sync_skill_version_resets_stale_latest(session) -> None:
    """_sync_skill_version 连写两个版本后：只剩一条 is_latest=true，快照指纹跟到新版本。"""
    skill_id = f'huanxing/test/sync-latest-{_tag()}'
    skill = await _seed_common_skill(session, skill_id)

    async def _sync(version: str, content_hash: str) -> None:
        await github_sync_service._sync_skill_version(
            session,
            skill.id,
            skill_id,
            {'version': version, 'content_hash': content_hash, 'is_latest': True},
        )
        await session.flush()

    await _sync('1.0.0', 'fp-v1')
    await _sync('1.1.0', 'fp-v2')

    rows = (
        await session.execute(
            select(MarketplaceSkillVersion.version, MarketplaceSkillVersion.is_latest)
            .where(MarketplaceSkillVersion.skill_id == skill_id)
            .order_by(MarketplaceSkillVersion.id)
        )
    ).all()
    assert [(v, latest) for v, latest in rows] == [('1.0.0', False), ('1.1.0', True)]

    fingerprints = await get_skills_content_fingerprints(session, [skill_id])
    assert fingerprints[skill_id] == 'fp-v2'

    # 同版本重跑（webhook 重放）幂等：仍只有一条 latest。
    await _sync('1.1.0', 'fp-v2')
    latest_count = (
        await session.execute(
            select(MarketplaceSkillVersion.id).where(
                MarketplaceSkillVersion.skill_id == skill_id,
                MarketplaceSkillVersion.is_latest.is_(True),
            )
        )
    ).all()
    assert len(latest_count) == 1


async def test_bundle_fingerprints_pick_latest_row_and_stable(session) -> None:
    """bundle 侧对称（评审 D3）：恒取 is_latest 版本行指纹，revision 稳定。"""
    template_id = f'huanxing/test/bundle-rev-{_tag()}'
    session.add(
        MarketplaceTemplate(
            template_id=template_id,
            namespace='huanxing',
            slug=template_id.rsplit('/', 1)[-1],
            name='revision 稳定性测试包',
            template_type='skill_pack',
            status='published',
            visibility='public',
            is_common=True,
            is_private=False,
            price=Decimal(0),
        )
    )
    session.add_all(
        [
            MarketplaceTemplateVersion(template_id=template_id, version='1.0.0', content_hash='bundle-fp-old', is_latest=False),
            MarketplaceTemplateVersion(template_id=template_id, version='1.1.0', content_hash='bundle-fp-current', is_latest=True),
        ]
    )
    await session.flush()

    lines = await _common_bundle_fingerprints(session)
    assert f'bundle:{template_id}@bundle-fp-current' in lines

    _, revision = await get_common_skill_snapshot(session)
    for _ in range(_SNAPSHOT_REPEAT):
        assert (await get_common_skill_snapshot(session))[1] == revision


async def test_partial_unique_index_blocks_second_latest_row(session) -> None:
    """partial unique index 写入期拦截第二条 is_latest=true 行（迁移未执行则 skip）。"""
    index_exists = (
        await session.execute(
            text(
                "SELECT 1 FROM pg_indexes WHERE schemaname = 'hasn_marketplace' "
                "AND indexname = 'uq_marketplace_skill_version_latest'"
            )
        )
    ).first()
    if not index_exists:
        pytest.skip('本地库未执行 2026-07-02-skill-version-latest-unique.sql 迁移，跳过索引兜底断言')

    skill_id = f'huanxing/test/uq-latest-{_tag()}'
    await _seed_common_skill(session, skill_id)
    session.add(_version_row(skill_id, '1.0.0', 'fp-a', is_latest=True))
    await session.flush()

    # 本测试最后一步：违约后 session 进入 rollback-required 态，由 fixture 统一 rollback。
    session.add(_version_row(skill_id, '1.1.0', 'fp-b', is_latest=True))
    with pytest.raises(IntegrityError):
        await session.flush()

"""个人技能表 backfill 迁移 真实 PG 测试（SKILLSYNC-C1）。

验证 backfill 迁移把 marketplace_skill 里 source_type='user' 的个人技能正确、幂等地
回填进 marketplace_personal_skill：
  1) 字段映射正确（origin/name/body/package_url/published_skill_id/visibility）。
  2) 幂等：重复执行不产生重复行。
  3) 非 user 来源（github/clawhub）不被回填。

需要 export DATABASE_PORT=15432。
"""

from __future__ import annotations

import uuid

from pathlib import Path

import pytest
import pytest_asyncio

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.marketplace.model.marketplace_personal_skill import MarketplacePersonalSkill
from backend.app.marketplace.model.marketplace_skill import MarketplaceSkill
from backend.app.marketplace.model.marketplace_skill_version import MarketplaceSkillVersion
from backend.database.db import SQLALCHEMY_DATABASE_URL

# 迁移 SQL：backend/tests/marketplace/<file> → parents[2]=backend
_BACKFILL_SQL = (
    Path(__file__).resolve().parents[2]
    / 'sql' / 'marketplace' / 'migrations' / '2026-06-12-backfill-personal-skill.sql'
).read_text(encoding='utf-8')


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _cleanup(session, skill_id, user_id, slug):
    await session.execute(delete(MarketplaceSkillVersion).where(MarketplaceSkillVersion.skill_id == skill_id))
    await session.execute(delete(MarketplaceSkill).where(MarketplaceSkill.skill_id == skill_id))
    await session.execute(
        delete(MarketplacePersonalSkill).where(
            (MarketplacePersonalSkill.user_id == user_id) & (MarketplacePersonalSkill.slug == slug)
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_backfill_maps_user_skill_into_personal_skill(db_session):
    tag = uuid.uuid4().hex[:8]
    user_id = 990000 + (int(tag, 16) % 100000)
    slug = f'pskill-{tag}'
    namespace = f'user/h_test_{tag}'
    skill_id = f'{namespace}/{slug}'
    try:
        db_session.add(
            MarketplaceSkill(
                skill_id=skill_id, namespace=namespace, slug=slug, user_id=user_id,
                hasn_id=f'h_test_{tag}', name='我的个人技能', description_zh='中文描述',
                body_zh='# 我的技能\n\n中文正文。', files='[{"path":"SKILL.md","size":42}]',
                source_type='user', status='published', visibility='private',
            )
        )
        db_session.add(
            MarketplaceSkillVersion(
                skill_id=skill_id, version='1.0.0', package_url='https://s3/marketplace/skills/x.zip',
                file_hash='abc123', file_size=2048, is_latest=True,
            )
        )
        await db_session.commit()

        # 跑两遍验证幂等
        await db_session.execute(text(_BACKFILL_SQL))
        await db_session.execute(text(_BACKFILL_SQL))
        await db_session.commit()

        rows = (
            await db_session.execute(
                select(MarketplacePersonalSkill).where(
                    (MarketplacePersonalSkill.user_id == user_id)
                    & (MarketplacePersonalSkill.slug == slug)
                )
            )
        ).scalars().all()
        assert len(rows) == 1, f'幂等失败，回填了 {len(rows)} 行'
        ps = rows[0]
        assert ps.origin == 'user-upload'
        assert ps.name == '我的个人技能'
        assert ps.body == '# 我的技能\n\n中文正文。'
        assert ps.description == '中文描述'
        assert ps.package_url == 'https://s3/marketplace/skills/x.zip'
        assert ps.file_hash == 'abc123'
        assert ps.file_size == 2048
        assert ps.visibility == 'private'
        # 已 published → published_skill_id 回指原 skill_id
        assert ps.published_skill_id == skill_id
        assert ps.personal_skill_id == skill_id
    finally:
        await _cleanup(db_session, skill_id, user_id, slug)


@pytest.mark.asyncio
async def test_backfill_skips_non_user_skills(db_session):
    tag = uuid.uuid4().hex[:8]
    user_id = 880000 + (int(tag, 16) % 100000)
    slug = f'ghskill-{tag}'
    namespace = 'huanxing/clawhub'
    skill_id = f'{namespace}/{slug}'
    try:
        db_session.add(
            MarketplaceSkill(
                skill_id=skill_id, namespace=namespace, slug=slug, user_id=user_id,
                name='平台技能', source_type='github', status='published', visibility='public',
            )
        )
        await db_session.commit()

        await db_session.execute(text(_BACKFILL_SQL))
        await db_session.commit()

        rows = (
            await db_session.execute(
                select(MarketplacePersonalSkill).where(MarketplacePersonalSkill.slug == slug)
            )
        ).scalars().all()
        assert rows == [], 'github 来源不应被回填进个人技能表'
    finally:
        await _cleanup(db_session, skill_id, user_id, slug)

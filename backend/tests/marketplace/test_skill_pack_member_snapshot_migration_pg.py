"""技能包成员快照迁移的真实 PostgreSQL 回归测试。"""

from __future__ import annotations

import hashlib
import uuid

from decimal import Decimal
from pathlib import Path

import pytest

from sqlalchemy import select, text

from backend.app.marketplace.model import (
    MarketplaceSkill,
    MarketplaceSkillVersion,
    MarketplaceTemplate,
    MarketplaceTemplateVersion,
)
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio

_MIGRATION = (
    Path(__file__).parents[2]
    / 'sql/marketplace/migrations/2026-07-29-freeze-skill-pack-member-snapshots.sql'
)


async def test_migration_freezes_every_resolvable_member_on_postgresql() -> None:
    """迁移可在 PostgreSQL 执行，并把每个可解析成员冻结为版本与内容指纹。"""
    tag = uuid.uuid4().hex[:10]
    first_skill_id = f'huanxing/{tag}-first'
    second_skill_id = f'huanxing/{tag}-second'
    template_id = f'huanxing/{tag}-pack'
    first_hash = hashlib.sha256(first_skill_id.encode()).hexdigest()
    second_hash = hashlib.sha256(second_skill_id.encode()).hexdigest()

    async with async_db_session() as db:
        transaction = await db.begin()
        try:
            db.add_all(
                [
                    MarketplaceSkill(
                        skill_id=first_skill_id,
                        namespace='huanxing',
                        slug=f'{tag}-first',
                        name='第一个迁移测试技能',
                        source_type='huanxing',
                    ),
                    MarketplaceSkill(
                        skill_id=second_skill_id,
                        namespace='huanxing',
                        slug=f'{tag}-second',
                        name='第二个迁移测试技能',
                        source_type='huanxing',
                    ),
                    MarketplaceSkillVersion(
                        skill_id=first_skill_id,
                        version='1.0.0',
                        content_hash=first_hash,
                        file_hash=first_hash,
                        is_latest=True,
                    ),
                    MarketplaceSkillVersion(
                        skill_id=second_skill_id,
                        version='2.0.0',
                        content_hash=None,
                        file_hash=second_hash,
                        is_latest=True,
                    ),
                    MarketplaceTemplate(
                        template_id=template_id,
                        namespace='huanxing',
                        slug=f'{tag}-pack',
                        template_type='skill_pack',
                        name='迁移测试技能包',
                        pricing_type='free',
                        price=Decimal(0),
                        is_private=False,
                    ),
                    MarketplaceTemplateVersion(
                        template_id=template_id,
                        version='1.0.0',
                        skill_dependencies_versioned={
                            first_skill_id: '*',
                            second_skill_id: {'version': '2.0.0'},
                        },
                        is_latest=True,
                    ),
                ]
            )
            await db.flush()

            migration_sql = text(_MIGRATION.read_text(encoding='utf-8'))
            await db.execute(migration_sql)
            await db.execute(migration_sql)

            frozen = await db.scalar(
                select(MarketplaceTemplateVersion.skill_dependencies_versioned).where(
                    MarketplaceTemplateVersion.template_id == template_id,
                    MarketplaceTemplateVersion.version == '1.0.0',
                )
            )
            assert frozen == {
                first_skill_id: {
                    'version': '1.0.0',
                    'content_hash': first_hash,
                },
                second_skill_id: {
                    'version': '2.0.0',
                    'content_hash': second_hash,
                },
            }
        finally:
            await transaction.rollback()

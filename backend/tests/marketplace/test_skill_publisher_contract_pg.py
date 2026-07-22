"""技能发布器与市场技能模型的真实 PostgreSQL 契约回归测试。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from backend.cli_tools.publisher.skill_publisher import SkillPublisher
from backend.cli_tools.validator.skill_validator import SkillConfig
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')


async def test_skill_publisher_stores_localized_skill_fields(tmp_path: Path) -> None:
    """本地发布器创建的技能必须使用市场模型实际存在的双语字段。"""
    skill_id = f'quality-publisher-{uuid4().hex[:8]}'
    config = SkillConfig(
        id=skill_id,
        name='质量发布技能',
        version='1.0.0',
        description='验证发布器字段映射',
        category='quality',
        tags='quality,contract',
        author_name='质量门禁',
    )

    async with async_db_session() as db:
        publisher = SkillPublisher(tmp_path)
        await publisher._create_skill(db, config, icon_url=None)
        skill = await publisher._get_skill(db, skill_id)
        assert skill is not None
        assert skill.name_en == config.name
        assert skill.name_zh == config.name
        assert skill.description_en == config.description
        assert skill.description_zh == config.description

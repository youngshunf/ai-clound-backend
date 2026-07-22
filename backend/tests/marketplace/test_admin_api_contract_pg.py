"""市场管理端 API 的真实 PostgreSQL 响应契约回归测试。"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from backend.app.marketplace.api.v1.admin.marketplace_skill import get_marketplace_skill
from backend.app.marketplace.api.v1.admin.marketplace_template import get_marketplace_template
from backend.app.marketplace.model import MarketplaceSkill, MarketplaceTemplate
from backend.app.marketplace.schema.marketplace_skill import GetMarketplaceSkillDetail
from backend.app.marketplace.schema.marketplace_template import GetMarketplaceTemplateDetail
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')


async def test_admin_marketplace_detail_apis_return_declared_dtos() -> None:
    """详情接口必须在真实 ORM 实体与公开 DTO 之间完成显式转换。"""
    suffix = uuid4().hex[:8]
    namespace = f'quality/admin-{suffix}'
    template_id = f'{namespace}/template'
    skill_id = f'{namespace}/skill'

    async with async_db_session() as db:
        template = MarketplaceTemplate(
            template_id=template_id,
            namespace=namespace,
            slug='template',
            template_type='agent_template',
            name='质量模板',
            pricing_type='free',
            price=Decimal(0),
            is_private=True,
            is_official=False,
            download_count=0,
        )
        skill = MarketplaceSkill(
            skill_id=skill_id,
            namespace=namespace,
            slug='skill',
            name='质量技能',
            pricing_type='free',
            price=Decimal(0),
            is_private=True,
            is_official=False,
            download_count=0,
        )
        db.add_all([template, skill])
        await db.flush()

        template_response = await get_marketplace_template(db, template_id)
        skill_response = await get_marketplace_skill(db, skill_id)

        assert isinstance(template_response.data, GetMarketplaceTemplateDetail)
        assert template_response.data.template_id == template_id
        assert isinstance(skill_response.data, GetMarketplaceSkillDetail)
        assert skill_response.data.skill_id == skill_id

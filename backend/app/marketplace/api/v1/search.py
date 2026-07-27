"""技能市场搜索 API

支持关键词搜索技能和模板
"""
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import or_, select

from backend.app.marketplace.model import MarketplaceSkill, MarketplaceTemplate
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


class SearchSkillItem(BaseModel):
    """搜索结果 - 技能"""
    id: int
    skill_id: str
    name: str
    description: str | None
    icon_url: str | None
    emoji: str | None
    author_name: str | None
    category: str | None
    tags: str | None
    pricing_type: str
    download_count: int
    is_official: bool


class SearchTemplateItem(BaseModel):
    """搜索结果 - 模板"""
    id: int
    template_id: str
    name: str
    description: str | None
    icon_url: str | None
    emoji: str | None
    author_name: str | None
    category: str | None
    tags: str | None
    pricing_type: str
    download_count: int
    is_official: bool


class SearchResponse(BaseModel):
    """搜索响应"""
    skills: list[SearchSkillItem]
    templates: list[SearchTemplateItem]
    total_skills: int
    total_templates: int


@router.get(
    '',
    summary='搜索技能和模板',
    description='根据关键词搜索技能和模板，支持名称、描述、标签匹配',
)
async def search(
    db: CurrentSession,
    q: Annotated[str, Query(description='搜索关键词', min_length=1)],
    type: Annotated[str | None, Query(description='类型筛选: skill/template/all')] = 'all',
    category: Annotated[str | None, Query(description='分类筛选')] = None,
    limit: Annotated[int, Query(description='每类最大返回数量', ge=1, le=100)] = 20,
) -> ResponseSchemaModel[SearchResponse]:
    skills = []
    templates = []
    total_skills = 0
    total_templates = 0

    search_pattern = f'%{q}%'

    # 搜索技能
    if type in ('skill', 'all'):
        skill_query = select(MarketplaceSkill).where(
            MarketplaceSkill.status == 'published',
            MarketplaceSkill.visibility == 'public',
            or_(
                MarketplaceSkill.name_zh.ilike(search_pattern),
                MarketplaceSkill.name_en.ilike(search_pattern),
                MarketplaceSkill.description_zh.ilike(search_pattern),
                MarketplaceSkill.description_en.ilike(search_pattern),
                MarketplaceSkill.skill_id.ilike(search_pattern),
                MarketplaceSkill.tags.ilike(search_pattern),
                MarketplaceSkill.tags_en.ilike(search_pattern),
                MarketplaceSkill.tags_zh.ilike(search_pattern),
                MarketplaceSkill.category.ilike(search_pattern),
            )
        )

        if category:
            skill_query = skill_query.where(MarketplaceSkill.category == category)

        skill_query = skill_query.order_by(
            MarketplaceSkill.is_official.desc(),
            MarketplaceSkill.download_count.desc()
        ).limit(limit)

        skill_result = await db.execute(skill_query)
        skill_models = skill_result.scalars().all()

        skills = [
            SearchSkillItem(
                id=s.id,
                skill_id=s.skill_id,
                name=s.name_zh or s.name_en or s.skill_id,
                description=s.description_zh or s.description_en,
                icon_url=s.icon_url,
                emoji=s.emoji,
                author_name=s.author_name,
                category=s.category,
                tags=s.tags,
                pricing_type=s.pricing_type,
                download_count=s.download_count,
                is_official=s.is_official,
            )
            for s in skill_models
        ]
        total_skills = len(skills)

    # 搜索模板
    if type in ('template', 'all'):
        template_query = select(MarketplaceTemplate).where(
            MarketplaceTemplate.status == 'published',
            MarketplaceTemplate.visibility == 'public',
            or_(
                MarketplaceTemplate.name.ilike(search_pattern),
                MarketplaceTemplate.description.ilike(search_pattern),
                MarketplaceTemplate.name_zh.ilike(search_pattern),
                MarketplaceTemplate.name_en.ilike(search_pattern),
                MarketplaceTemplate.description_zh.ilike(search_pattern),
                MarketplaceTemplate.description_en.ilike(search_pattern),
                MarketplaceTemplate.template_id.ilike(search_pattern),
                MarketplaceTemplate.tags.ilike(search_pattern),
                MarketplaceTemplate.category.ilike(search_pattern),
            )
        )

        if category:
            template_query = template_query.where(MarketplaceTemplate.category == category)

        template_query = template_query.order_by(
            MarketplaceTemplate.is_official.desc(),
            MarketplaceTemplate.download_count.desc()
        ).limit(limit)

        template_result = await db.execute(template_query)
        template_models = template_result.scalars().all()

        templates = [
            SearchTemplateItem(
                id=template.id,
                template_id=template.template_id,
                name=template.name,
                description=template.description,
                icon_url=template.icon_url,
                emoji=template.emoji,
                author_name=template.author_name,
                category=template.category,
                tags=template.tags,
                pricing_type=template.pricing_type,
                download_count=template.download_count,
                is_official=template.is_official,
            )
            for template in template_models
        ]
        total_templates = len(templates)

    return response_base.success(data=SearchResponse(
        skills=skills,
        templates=templates,
        total_skills=total_skills,
        total_templates=total_templates,
    ))

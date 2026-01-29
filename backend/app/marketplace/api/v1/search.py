"""技能市场搜索 API

支持关键词搜索技能和应用
"""
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select, or_

from backend.app.marketplace.model import MarketplaceSkill, MarketplaceApp
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
    author_name: str | None
    category: str | None
    tags: str | None
    pricing_type: str
    download_count: int
    is_official: bool


class SearchAppItem(BaseModel):
    """搜索结果 - 应用"""
    id: int
    app_id: str
    name: str
    description: str | None
    icon_url: str | None
    author_name: str | None
    pricing_type: str
    download_count: int
    is_official: bool


class SearchResponse(BaseModel):
    """搜索响应"""
    skills: list[SearchSkillItem]
    apps: list[SearchAppItem]
    total_skills: int
    total_apps: int


@router.get(
    '',
    summary='搜索技能和应用',
    description='根据关键词搜索技能和应用，支持名称、描述、标签匹配',
)
async def search(
    db: CurrentSession,
    q: Annotated[str, Query(description='搜索关键词', min_length=1)],
    type: Annotated[str | None, Query(description='类型筛选: skill/app/all')] = 'all',
    category: Annotated[str | None, Query(description='分类筛选')] = None,
    limit: Annotated[int, Query(description='每类最大返回数量', ge=1, le=100)] = 20,
) -> ResponseSchemaModel[SearchResponse]:
    skills = []
    apps = []
    total_skills = 0
    total_apps = 0
    
    search_pattern = f'%{q}%'
    
    # 搜索技能
    if type in ('skill', 'all'):
        skill_query = select(MarketplaceSkill).where(
            MarketplaceSkill.is_private == False,
            or_(
                MarketplaceSkill.name.ilike(search_pattern),
                MarketplaceSkill.description.ilike(search_pattern),
                MarketplaceSkill.tags.ilike(search_pattern),
                MarketplaceSkill.skill_id.ilike(search_pattern),
            )
        )
        
        if category:
            skill_query = skill_query.where(MarketplaceSkill.category == category)
        
        skill_query = skill_query.order_by(
            MarketplaceSkill.is_official.desc(),
            MarketplaceSkill.download_count.desc()
        ).limit(limit)
        
        result = await db.execute(skill_query)
        skill_models = result.scalars().all()
        
        skills = [
            SearchSkillItem(
                id=s.id,
                skill_id=s.skill_id,
                name=s.name,
                description=s.description,
                icon_url=s.icon_url,
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
    
    # 搜索应用
    if type in ('app', 'all'):
        app_query = select(MarketplaceApp).where(
            MarketplaceApp.is_private == False,
            or_(
                MarketplaceApp.name.ilike(search_pattern),
                MarketplaceApp.description.ilike(search_pattern),
                MarketplaceApp.app_id.ilike(search_pattern),
            )
        )
        
        app_query = app_query.order_by(
            MarketplaceApp.is_official.desc(),
            MarketplaceApp.download_count.desc()
        ).limit(limit)
        
        result = await db.execute(app_query)
        app_models = result.scalars().all()
        
        apps = [
            SearchAppItem(
                id=a.id,
                app_id=a.app_id,
                name=a.name,
                description=a.description,
                icon_url=a.icon_url,
                author_name=a.author_name,
                pricing_type=a.pricing_type,
                download_count=a.download_count,
                is_official=a.is_official,
            )
            for a in app_models
        ]
        total_apps = len(apps)
    
    return response_base.success(data=SearchResponse(
        skills=skills,
        apps=apps,
        total_skills=total_skills,
        total_apps=total_apps,
    ))

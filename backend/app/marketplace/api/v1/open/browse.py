"""Marketplace public browse API."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query

from backend.app.marketplace.service.search_service import search_service
from backend.database.db import CurrentSession

router = APIRouter()


@router.get('/trending/skills', summary='Trending public skills')
async def trending_skills(
    db: CurrentSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    lang: Annotated[str, Query()] = 'zh',
) -> dict[str, Any]:
    return {
        'items': await search_service.get_popular_skills(db=db, lang=lang, limit=limit),
        'limit': limit,
    }


@router.get('/trending/templates', summary='Trending public templates')
async def trending_templates(
    db: CurrentSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> dict[str, Any]:
    return {
        'items': await search_service.get_trending_templates(db=db, limit=limit),
        'limit': limit,
    }


@router.get('/categories', summary='Public marketplace categories')
async def list_categories(db: CurrentSession) -> dict[str, Any]:
    return {'items': await search_service.get_marketplace_categories(db=db)}


@router.get('/categories/{category_slug}/skills', summary='Public skills by category')
async def category_skills(
    db: CurrentSession,
    category_slug: str,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    lang: Annotated[str, Query()] = 'zh',
) -> dict[str, Any]:
    return await search_service.search_skills(
        db=db,
        category=category_slug,
        page=page,
        page_size=page_size,
        lang=lang,
    )


@router.get('/categories/{category_slug}/templates', summary='Public templates by category')
async def category_templates(
    db: CurrentSession,
    category_slug: str,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    return await search_service.search_templates(
        db=db,
        category=category_slug,
        page=page,
        page_size=page_size,
    )

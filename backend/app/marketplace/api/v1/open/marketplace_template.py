"""Marketplace templates public API."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from backend.app.marketplace.crud.crud_marketplace_template import marketplace_template_dao
from backend.app.marketplace.crud.crud_marketplace_template_version import marketplace_template_version_dao
from backend.app.marketplace.service.marketplace_template_service import marketplace_template_service
from backend.app.marketplace.service.search_service import search_service
from backend.database.db import CurrentSession

router = APIRouter()


@router.get('', summary='List public templates')
async def list_templates(
    db: CurrentSession,
    category: Annotated[str | None, Query()] = None,
    keyword: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    source_type: Annotated[str | None, Query()] = None,
    namespace: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    sort: Annotated[str | None, Query()] = None,
    sort_by: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    return await search_service.search_templates(
        db=db,
        keyword=keyword or q,
        category=category,
        source_type=source_type,
        namespace=namespace,
        page=page,
        page_size=page_size,
        sort_by=sort or sort_by or 'popular',
    )


@router.get('/search', summary='Search public templates')
async def search_templates(
    db: CurrentSession,
    keyword: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    source_type: Annotated[str | None, Query()] = None,
    namespace: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    sort: Annotated[str | None, Query()] = None,
    sort_by: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    return await search_service.search_templates(
        db=db,
        keyword=keyword or q,
        category=category,
        source_type=source_type,
        namespace=namespace,
        page=page,
        page_size=page_size,
        sort_by=sort or sort_by or 'popular',
    )


@router.get('/{resource_id:path}/download', summary='Download public template package')
async def download_template_open(
    db: CurrentSession,
    resource_id: str,
    version: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    template = await marketplace_template_service.get_by_resource_id_public(db=db, resource_id=resource_id)
    template_id = template.template_id
    if version:
        template_version = await marketplace_template_version_dao.get_by_template_and_version(db, template_id, version)
    else:
        template_version = await marketplace_template_version_dao.get_latest_by_template(db, template_id)
    if not template_version or not template_version.package_url:
        raise HTTPException(status_code=404, detail='Template package not found')
    await marketplace_template_dao.increment_download_count(db, template_id)
    await db.commit()
    return RedirectResponse(url=template_version.package_url, status_code=302)


@router.get('/{resource_id:path}', summary='Get public template detail')
async def get_template_detail(db: CurrentSession, resource_id: str) -> dict[str, Any]:
    template = await marketplace_template_service.get_by_resource_id_public(db=db, resource_id=resource_id)
    return marketplace_template_service.format_template(template)

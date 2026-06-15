"""Marketplace skills public API."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse, StreamingResponse

from backend.app.marketplace.crud.crud_marketplace_download import marketplace_download_dao
from backend.app.marketplace.crud.crud_marketplace_skill import marketplace_skill_dao
from backend.app.marketplace.crud.crud_marketplace_skill_version import marketplace_skill_version_dao
from backend.app.marketplace.schema.marketplace_download import CreateMarketplaceDownloadParam
from backend.app.marketplace.service.marketplace_skill_service import marketplace_skill_service
from backend.app.marketplace.service.package_service import package_service
from backend.app.marketplace.service.search_service import search_service
from backend.database.db import CurrentSession  # noqa: TC001

router = APIRouter()


@router.get('', summary='List public skills')
async def list_skills(
    db: CurrentSession,
    category: Annotated[str | None, Query()] = None,
    tags: Annotated[str | None, Query()] = None,
    source_type: Annotated[str | None, Query()] = None,
    namespace: Annotated[str | None, Query()] = None,
    lang: Annotated[str, Query()] = 'zh',
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    sort: Annotated[str | None, Query()] = None,
    sort_by: Annotated[str | None, Query()] = None,
    skill_ids: Annotated[str | None, Query(description='逗号分隔 skill_id，仅返回这些技能（批量解析元数据用）')] = None,
    is_common: Annotated[bool | None, Query(description='只取公共技能(true)/只取非公共(false)/不过滤(省略)')] = None,
) -> dict[str, Any]:
    tag_list = [tag.strip() for tag in tags.split(',')] if tags else None
    skill_id_list = [sid.strip() for sid in skill_ids.split(',') if sid.strip()] if skill_ids else None
    return await search_service.search_skills(
        db=db,
        category=category,
        tags=tag_list,
        source_type=source_type,
        namespace=namespace,
        lang=lang,
        page=page,
        page_size=page_size,
        sort_by=sort or sort_by or 'popular',
        skill_ids=skill_id_list,
        is_common=is_common,
    )


@router.get('/search', summary='Search public skills')
async def search_skills(
    db: CurrentSession,
    keyword: Annotated[str | None, Query(description='Search keyword')] = None,
    q: Annotated[str | None, Query(description='Search keyword alias')] = None,
    category: Annotated[str | None, Query(description='Category filter')] = None,
    tags: Annotated[str | None, Query(description='Comma-separated tags')] = None,
    source_type: Annotated[str | None, Query(description='Source type filter')] = None,
    namespace: Annotated[str | None, Query(description='Namespace filter')] = None,
    lang: Annotated[str, Query(description='Language (zh/en)')] = 'zh',
    page: Annotated[int, Query(ge=1, description='Page number')] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description='Items per page')] = 20,
    sort: Annotated[str | None, Query(description='Sort by (popular/latest/downloads/stars)')] = None,
    sort_by: Annotated[str | None, Query(description='Sort by alias (popular/latest/downloads/stars)')] = None,
) -> dict[str, Any]:
    tag_list = [tag.strip() for tag in tags.split(',')] if tags else None
    return await search_service.search_skills(
        db=db,
        keyword=keyword or q,
        category=category,
        tags=tag_list,
        source_type=source_type,
        namespace=namespace,
        lang=lang,
        page=page,
        page_size=page_size,
        sort_by=sort or sort_by or 'popular',
    )


@router.get(
    '/{resource_id:path}/download',
    summary='Download public skill package',
    response_model=None,
)
async def download_skill_open(
    db: CurrentSession,
    resource_id: str,
    version: Annotated[str | None, Query(description='Version (use latest if not specified)')] = None,
) -> RedirectResponse | StreamingResponse:
    skill = await marketplace_skill_service.get_by_resource_id_public(db=db, resource_id=resource_id)
    skill_id = skill.skill_id
    if version:
        skill_version = await marketplace_skill_version_dao.get_by_skill_and_version(db, skill_id, version)
    else:
        skill_version = await marketplace_skill_version_dao.get_latest_by_skill(db, skill_id)
        version = skill_version.version if skill_version else None

    if not skill_version:
        raise HTTPException(status_code=404, detail='Skill version not found')

    if skill_version.package_url:
        await marketplace_download_dao.create(
            db,
            CreateMarketplaceDownloadParam(
                resource_type='skill',
                resource_id=skill_id,
                resource_name=skill.name_zh or skill.name_en,
                version=skill_version.version,
                download_source='web',
                user_id=0,
                ip_address=None,
                user_agent=None,
            ),
        )
        await marketplace_skill_dao.increment_download_count(db, skill_id)
        await db.commit()
        return RedirectResponse(url=skill_version.package_url, status_code=302)

    try:
        package_path, package_hash = await package_service.get_skill_package(db, skill_id, version)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    await marketplace_download_dao.create(
        db,
        CreateMarketplaceDownloadParam(
            resource_type='skill',
            resource_id=skill_id,
            resource_name=skill.name_zh or skill.name_en,
            version=version or '',
            download_source='web',
            user_id=0,
            ip_address=None,
            user_agent=None,
        ),
    )
    await marketplace_skill_dao.increment_download_count(db, skill_id)
    await db.commit()

    file_stream = package_service.get_package_stream(package_path)
    filename = f"{skill_id.replace('/', '_')}_{version}.zip"
    return StreamingResponse(
        file_stream,
        media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{filename}"', 'X-Package-Hash': package_hash},
    )


@router.get('/{resource_id:path}', summary='Get public skill detail')
async def get_skill_detail(
    db: CurrentSession,
    resource_id: str,
    lang: Annotated[str, Query(description='Language (zh/en)')] = 'zh',
) -> dict[str, Any]:
    detail = await search_service.get_skill_detail(db, resource_id, lang)
    if not detail:
        raise HTTPException(status_code=404, detail='Skill not found')
    return detail

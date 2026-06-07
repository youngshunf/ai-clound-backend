from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Query, Request

from backend.app.marketplace.schema.skill_pack import (
    SkillPackCreateRequest,
    SkillPackPage,
    SkillPackResponse,
)
from backend.app.marketplace.service import skill_pack_service
from backend.app.marketplace.service.marketplace_template_service import marketplace_template_service
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()

# 浏览选取的列（卡片渲染 + 安装所需 version 字段）。
_LIST_COLUMNS = '''
    t.template_id,
    t.namespace,
    t.slug,
    t.name,
    t.description,
    t.category,
    t.tags,
    t.icon_url,
    t.emoji,
    t.source_type,
    t.is_official,
    t.download_count,
    t.status,
    t.visibility,
    t.author_name,
    v.version,
    v.bundle_slug,
    v.command_key,
    v.hermes_bundle_json,
    v.hermes_yaml,
    COALESCE(v.content_hash, v.file_hash) AS content_hash,
    v.package_url,
    v.file_hash,
    v.published_at
'''


@router.get('', summary='List marketplace skill packs')
async def list_skill_packs(
    request: Request,
    db: CurrentSession,
    q: str | None = Query(default=None, description='关键词（名称/描述）'),
    category: str | None = Query(default=None, description='分类筛选'),
    sort: str = Query(default='popular', description='排序：popular/latest/downloads'),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=100),
    mine: bool = Query(default=False, description='true=只看自己的（含未发布草稿，用于「我的发布」）'),
) -> ResponseSchemaModel[SkillPackPage]:
    user_id = getattr(request.scope.get('user'), 'id', None)

    where = [
        "t.template_type = 'skill_pack'",
        'v.bundle_slug IS NOT NULL',
        'v.command_key IS NOT NULL',
        'v.hermes_yaml IS NOT NULL',
    ]
    params: dict[str, Any] = {'user_id': user_id}
    if mine:
        # 我的发布：自己的全部状态（草稿/待审/已发布…）。
        where.append('(t.author_id = :user_id OR t.user_id = :user_id)')
    else:
        # 市场浏览：已发布 + （公开 / 官方 / 自己）。
        where.append("t.status = 'published'")
        where.append('(t.is_private = false OR t.is_official = true OR t.author_id = :user_id)')
    if category:
        where.append('t.category = :category')
        params['category'] = category
    if q and q.strip():
        where.append('(t.name ILIKE :kw OR t.description ILIKE :kw)')
        params['kw'] = f'%{q.strip()}%'

    where_sql = ' AND '.join(where)
    order_sql = {
        'latest': 't.created_time DESC, t.id DESC',
        'downloads': 't.download_count DESC, t.id DESC',
    }.get(sort, 't.is_official DESC, t.download_count DESC, t.id DESC')

    count_row = await db.execute(
        sa.text(
            f'''
            SELECT count(*)
            FROM public.marketplace_template t
            JOIN public.marketplace_template_version v
              ON v.template_id = t.template_id AND v.is_latest = true
            WHERE {where_sql}
            '''
        ),
        params,
    )
    total = count_row.scalar() or 0

    params['limit'] = page_size
    params['offset'] = (page - 1) * page_size
    result = await db.execute(
        sa.text(
            f'''
            SELECT {_LIST_COLUMNS}
            FROM public.marketplace_template t
            JOIN public.marketplace_template_version v
              ON v.template_id = t.template_id AND v.is_latest = true
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT :limit OFFSET :offset
            '''
        ),
        params,
    )
    items = [_skill_pack_response(dict(row)) for row in result.mappings().all()]
    return response_base.success(
        data=SkillPackPage(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size if page_size else 1,
        )
    )


@router.post('', summary='Create or update marketplace skill pack', dependencies=[DependsJwtAuth])
async def create_skill_pack(
    request: Request,
    db: CurrentSessionTransaction,
    payload: SkillPackCreateRequest,
) -> ResponseSchemaModel[SkillPackResponse]:
    # 落库前 B2.2 校验 + 规范化 hermes_yaml；upsert 逻辑统一在 skill_pack_service（路由/MCP/同步共用）。
    snapshot = await skill_pack_service.upsert_skill_pack(
        db,
        payload,
        author_id=getattr(request.scope.get('user'), 'id', None),
    )
    return response_base.success(
        data=SkillPackResponse(
            template_id=snapshot['template_id'],
            version=snapshot['version'],
            name=snapshot['name'],
            description=snapshot['description'],
            bundle_slug=snapshot['bundle_slug'],
            command_key=snapshot['command_key'],
            hermes_bundle_json=snapshot['hermes_bundle_json'],
            hermes_yaml=snapshot['hermes_yaml'],
            content_hash=snapshot['content_hash'],
            file_hash=snapshot['file_hash'],
        )
    )


# ───────────────────────── 我的发布：提审 / 发布 / 下架 / 删除 ─────────────────────────
# 技能包是 marketplace_template(template_type='skill_pack') 行，发布工作流与模板同构，
# 直接复用 marketplace_template_service（按 template_id + user_id 操作，类型无关）。


@router.post('/{template_id:path}/submit-review', summary='Submit skill pack for review', dependencies=[DependsJwtAuth])
async def submit_skill_pack_review(request: Request, db: CurrentSessionTransaction, template_id: str) -> ResponseModel:
    template = await marketplace_template_service.submit_review(db=db, resource_id=template_id, user_id=request.user.id)
    return response_base.success(data=marketplace_template_service.format_template(template))


@router.post('/{template_id:path}/publish', summary='Publish skill pack', dependencies=[DependsJwtAuth])
async def publish_skill_pack(request: Request, db: CurrentSessionTransaction, template_id: str) -> ResponseModel:
    template = await marketplace_template_service.publish(db=db, resource_id=template_id, user_id=request.user.id)
    return response_base.success(data=marketplace_template_service.format_template(template))


@router.post('/{template_id:path}/unpublish', summary='Unpublish skill pack', dependencies=[DependsJwtAuth])
async def unpublish_skill_pack(request: Request, db: CurrentSessionTransaction, template_id: str) -> ResponseModel:
    template = await marketplace_template_service.unpublish(db=db, resource_id=template_id, user_id=request.user.id)
    return response_base.success(data=marketplace_template_service.format_template(template))


@router.delete('/{template_id:path}', summary='Delete skill pack', dependencies=[DependsJwtAuth])
async def delete_skill_pack(request: Request, db: CurrentSessionTransaction, template_id: str) -> ResponseModel:
    await marketplace_template_service.delete_user_template(db=db, resource_id=template_id, user_id=request.user.id)
    return response_base.success()


def _skill_pack_response(row: dict[str, Any]) -> SkillPackResponse:
    return SkillPackResponse(
        template_id=row['template_id'],
        version=row['version'],
        name=row['name'],
        description=row.get('description'),
        bundle_slug=row['bundle_slug'],
        command_key=row['command_key'],
        hermes_bundle_json=row.get('hermes_bundle_json'),
        hermes_yaml=row['hermes_yaml'],
        content_hash=row['content_hash'],
        package_url=row.get('package_url'),
        file_hash=row.get('file_hash'),
        published_at=row.get('published_at'),
        namespace=row.get('namespace'),
        slug=row.get('slug'),
        icon_url=row.get('icon_url'),
        emoji=row.get('emoji'),
        category=row.get('category'),
        tags=row.get('tags'),
        source_type=row.get('source_type'),
        is_official=row.get('is_official'),
        download_count=row.get('download_count'),
        status=row.get('status'),
        visibility=row.get('visibility'),
        author_name=row.get('author_name'),
    )

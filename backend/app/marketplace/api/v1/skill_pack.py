from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Request

from backend.app.marketplace.schema.skill_pack import SkillPackCreateRequest, SkillPackResponse
from backend.app.marketplace.service import skill_pack_service
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('', summary='List marketplace skill packs')
async def list_skill_packs(request: Request, db: CurrentSession) -> ResponseSchemaModel[list[SkillPackResponse]]:
    user_id = getattr(request.scope.get('user'), 'id', None)
    result = await db.execute(
        sa.text(
            '''
            SELECT
                t.template_id,
                t.name,
                t.description,
                v.version,
                v.bundle_slug,
                v.command_key,
                v.hermes_bundle_json,
                v.hermes_yaml,
                COALESCE(v.content_hash, v.file_hash) AS content_hash,
                v.package_url,
                v.file_hash,
                v.published_at
            FROM public.marketplace_template t
            JOIN public.marketplace_template_version v
              ON v.template_id = t.template_id
             AND v.is_latest = true
            WHERE t.template_type = 'skill_pack'
              AND (
                t.is_private = false
                OR t.is_official = true
                OR t.author_id = :user_id
              )
              AND v.bundle_slug IS NOT NULL
              AND v.command_key IS NOT NULL
              AND v.hermes_yaml IS NOT NULL
            ORDER BY t.is_official DESC, t.download_count DESC, t.id DESC
            '''
        ),
        {'user_id': user_id},
    )
    return response_base.success(data=[_skill_pack_response(dict(row)) for row in result.mappings().all()])


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
    )



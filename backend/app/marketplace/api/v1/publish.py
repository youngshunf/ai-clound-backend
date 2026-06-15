"""Marketplace publish API.

This API is used by CLI/agent upload flows with an LLM API key. It writes user
resources to the same draft/review state machine as the app-side API.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile
from pydantic import BaseModel
from sqlalchemy import select

from backend.app.admin.model.user import User
from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
from backend.app.newapi.apikey.service import api_key_service
from backend.app.marketplace.model import MarketplaceSkillVersion, MarketplaceTemplateVersion
from backend.app.marketplace.service.marketplace_skill_service import marketplace_skill_service
from backend.app.marketplace.service.marketplace_template_service import marketplace_template_service
from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.common.exception import errors
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession, CurrentSessionTransaction  # noqa: TC001

router = APIRouter()


@dataclass
class PublishUser:
    """Authenticated publisher."""

    user_id: int
    hasn_id: str
    username: str
    nickname: str


class PublishResult(BaseModel):
    """Publish result returned to CLI/agent clients."""

    id: str
    namespace: str
    slug: str
    status: str
    visibility: str
    user_id: int
    hasn_id: str
    version: str
    package_url: str
    file_hash: str
    file_size: int


async def verify_publish_api_key(
    db: CurrentSession,
    x_api_key: Annotated[str | None, Header(alias='X-API-Key')] = None,
) -> PublishUser:
    """Validate publish API key and resolve HASN identity."""
    if not x_api_key:
        raise errors.AuthorizationError(msg='缺少 API Key')

    api_key_record = await api_key_service.verify_api_key(db, x_api_key)
    result = await db.execute(select(User).where(User.id == api_key_record.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise errors.AuthorizationError(msg='用户不存在')

    hasn_human = await hasn_humans_dao.get_by_user_id(db, user_id=user.id)
    if not hasn_human:
        raise errors.AuthorizationError(msg='用户未注册 HASN 身份')

    return PublishUser(
        user_id=user.id,
        hasn_id=hasn_human.hasn_id,
        username=user.username,
        nickname=user.nickname,
    )


async def _latest_skill_version(db: CurrentSession, skill_id: str) -> MarketplaceSkillVersion:
    result = await db.execute(
        select(MarketplaceSkillVersion).where(
            MarketplaceSkillVersion.skill_id == skill_id,
            MarketplaceSkillVersion.is_latest,
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise errors.NotFoundError(msg='技能版本不存在')
    return version


async def _latest_template_version(db: CurrentSession, template_id: str) -> MarketplaceTemplateVersion:
    result = await db.execute(
        select(MarketplaceTemplateVersion).where(
            MarketplaceTemplateVersion.template_id == template_id,
            MarketplaceTemplateVersion.is_latest,
        )
    )
    version = result.scalar_one_or_none()
    if not version:
        raise errors.NotFoundError(msg='模板版本不存在')
    return version


@router.post('/upload-icon', summary='预上传图标')
async def upload_icon_only(
    db: CurrentSession,
    publish_user: Annotated[PublishUser, Depends(verify_publish_api_key)],
    file: Annotated[UploadFile, File(description='图标文件')],
    item_type: Annotated[str, Form(description='skill/template')] = 'skill',
    item_id: Annotated[str, Form(description='完整资源 ID，例如 user/{hasn_id}/{slug}')] = '',
) -> ResponseSchemaModel:
    if item_type not in {'skill', 'template'}:
        raise errors.RequestError(msg='图标类型仅支持 skill/template')
    if not item_id.startswith(f'user/{publish_user.hasn_id}/'):
        raise errors.AuthorizationError(msg='只能上传自己命名空间下的图标')

    content = await file.read()
    icon_url = await marketplace_storage_service.upload_icon_dedup(
        db=db,
        item_type=item_type,
        item_id=item_id,
        content=content,
        filename=file.filename or 'icon.svg',
    )
    return response_base.success(data={'icon_url': icon_url})


@router.post('/skill', summary='发布技能包')
async def publish_skill(
    db: CurrentSessionTransaction,
    publish_user: Annotated[PublishUser, Depends(verify_publish_api_key)],
    file: Annotated[UploadFile, File(description='技能包 ZIP 文件')],
    slug: Annotated[str | None, Form(description='公开 slug')] = None,
    changelog: Annotated[str | None, Form(description='更新日志')] = None,
) -> ResponseSchemaModel[PublishResult]:
    content = await file.read()
    skill = await marketplace_skill_service.upload_user_skill(
        db=db,
        user_id=publish_user.user_id,
        hasn_id=publish_user.hasn_id,
        content=content,
        filename=file.filename,
        slug=slug,
        changelog=changelog,
    )
    version = await _latest_skill_version(db, skill.skill_id)
    return response_base.success(
        data=PublishResult(
            id=skill.skill_id,
            namespace=skill.namespace or '',
            slug=skill.slug or '',
            status=skill.status,
            visibility=skill.visibility,
            user_id=skill.user_id or publish_user.user_id,
            hasn_id=skill.hasn_id or publish_user.hasn_id,
            version=version.version,
            package_url=version.package_url or '',
            file_hash=version.file_hash or '',
            file_size=version.file_size or 0,
        ),
    )


@router.post('/template', summary='发布模板包')
async def publish_template(
    db: CurrentSessionTransaction,
    publish_user: Annotated[PublishUser, Depends(verify_publish_api_key)],
    file: Annotated[UploadFile, File(description='模板包 ZIP 文件')],
    slug: Annotated[str | None, Form(description='公开 slug')] = None,
    changelog: Annotated[str | None, Form(description='更新日志')] = None,
) -> ResponseSchemaModel[PublishResult]:
    content = await file.read()
    template = await marketplace_template_service.upload_user_template(
        db=db,
        user_id=publish_user.user_id,
        hasn_id=publish_user.hasn_id,
        content=content,
        filename=file.filename,
        slug=slug,
        changelog=changelog,
    )
    version = await _latest_template_version(db, template.template_id)
    return response_base.success(
        data=PublishResult(
            id=template.template_id,
            namespace=template.namespace or '',
            slug=template.slug or '',
            status=template.status,
            visibility=template.visibility,
            user_id=template.user_id or publish_user.user_id,
            hasn_id=template.hasn_id or publish_user.hasn_id,
            version=version.version,
            package_url=version.package_url or '',
            file_hash=version.file_hash or '',
            file_size=version.file_size or 0,
        ),
    )

import hashlib
import json

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_skill import marketplace_skill_dao
from backend.app.marketplace.crud.crud_marketplace_skill_version import marketplace_skill_version_dao
from backend.app.marketplace.model import MarketplaceSkill, MarketplaceSkillVersion
from backend.app.marketplace.schema.marketplace_skill import (
    CreateMarketplaceSkillParam,
    DeleteMarketplaceSkillParam,
    UpdateMarketplaceSkillParam,
)
from backend.app.marketplace.service.package_validation import parse_skill_package
from backend.app.marketplace.service.resource_id import (
    build_resource_id,
    parse_resource_id,
    slug_from_candidate,
    validate_slug,
    validate_version,
)
from backend.app.marketplace.service.skill_content_extractor import extract_skill_body, raw_bilingual_body
from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.common.exception import errors
from backend.common.pagination import paging_data
from backend.utils.timezone import timezone

EDITABLE_FIELDS = {'name', 'description', 'category', 'tags', 'emoji', 'icon_url'}


def _normalize_tags_for_storage(tags: Any) -> str | None:
    if tags is None:
        return None
    if isinstance(tags, str):
        values = [item.strip() for item in tags.split(',')]
    elif isinstance(tags, list):
        values = [str(item).strip() for item in tags]
    else:
        values = [str(tags).strip()]
    return json.dumps([value for value in values if value], ensure_ascii=False)


def _localized_tags_for_storage(tags: Any) -> tuple[str | None, str | None, str | None]:
    stored = _normalize_tags_for_storage(tags)
    return stored, stored, stored


class MarketplaceSkillService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> MarketplaceSkill:
        """
        获取技能市场技能

        :param db: 数据库会话
        :param pk: 技能市场技能 ID
        :return:
        """
        marketplace_skill = await marketplace_skill_dao.get(db, pk)
        if not marketplace_skill:
            raise errors.NotFoundError(msg='技能市场技能不存在')
        return marketplace_skill

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取技能市场技能列表

        :param db: 数据库会话
        :return:
        """
        marketplace_skill_select = await marketplace_skill_dao.get_select()
        return await paging_data(db, marketplace_skill_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[MarketplaceSkill]:
        """
        获取所有技能市场技能

        :param db: 数据库会话
        :return:
        """
        marketplace_skills = await marketplace_skill_dao.get_all(db)
        return marketplace_skills

    @staticmethod
    def format_skill(skill: MarketplaceSkill) -> dict[str, Any]:
        return {
            'id': skill.id,
            'skill_id': skill.skill_id,
            'namespace': skill.namespace,
            'slug': skill.slug,
            'user_id': skill.user_id,
            'hasn_id': skill.hasn_id,
            'status': skill.status,
            'visibility': skill.visibility,
            'name': skill.name_zh or skill.name_en or skill.name,
            'name_en': skill.name_en,
            'name_zh': skill.name_zh,
            'description': skill.description_zh or skill.description_en,
            'description_en': skill.description_en,
            'description_zh': skill.description_zh,
            'icon_url': skill.icon_url,
            'emoji': skill.emoji,
            'author_name': skill.author_name,
            'category': skill.category,
            'tags': skill.tags,
            'tags_en': skill.tags_en,
            'tags_zh': skill.tags_zh,
            'source_type': skill.source_type,
            'pricing_type': skill.pricing_type,
            'price': float(skill.price) if skill.price is not None else 0,
            'download_count': skill.download_count,
            'star_count': skill.star_count,
            'is_official': skill.is_official,
            'reviewed_by': skill.reviewed_by,
            'reviewed_at': skill.reviewed_at.isoformat() if skill.reviewed_at else None,
            'review_note': skill.review_note,
            'published_at': skill.published_at.isoformat() if skill.published_at else None,
            'created_time': skill.created_time.isoformat() if skill.created_time else None,
            'updated_time': skill.updated_time.isoformat() if skill.updated_time else None,
        }

    @staticmethod
    async def get_by_resource_id_public(*, db: AsyncSession, resource_id: str) -> MarketplaceSkill:
        namespace, slug = parse_resource_id(resource_id)
        skill = await marketplace_skill_dao.get_by_namespace_slug_public(db, namespace, slug)
        if not skill:
            raise errors.NotFoundError(msg='技能不存在')
        return skill

    @staticmethod
    async def get_by_resource_id_for_user(*, db: AsyncSession, resource_id: str, user_id: int) -> MarketplaceSkill:
        namespace, slug = parse_resource_id(resource_id)
        skill = await marketplace_skill_dao.get_by_namespace_slug_for_user(db, namespace, slug, user_id)
        if not skill:
            raise errors.NotFoundError(msg='技能不存在')
        return skill

    @staticmethod
    async def get_by_resource_id_admin(*, db: AsyncSession, resource_id: str) -> MarketplaceSkill:
        namespace, slug = parse_resource_id(resource_id)
        skill = await marketplace_skill_dao.get_by_namespace_slug(db, namespace, slug)
        if not skill:
            raise errors.NotFoundError(msg='技能不存在')
        return skill

    @staticmethod
    async def list_user_skills(*, db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
        skills = await marketplace_skill_dao.get_by_user(db, user_id)
        return [MarketplaceSkillService.format_skill(skill) for skill in skills]

    @staticmethod
    async def upload_user_skill(
        *,
        db: AsyncSession,
        user_id: int,
        hasn_id: str,
        content: bytes,
        filename: str | None = None,
        slug: str | None = None,
        changelog: str | None = None,
        requested_visibility: str = 'private',
        commit: bool = True,
    ) -> MarketplaceSkill:
        if not hasn_id:
            raise errors.AuthorizationError(msg='用户未注册 HASN 身份')
        package = parse_skill_package(content)
        metadata = package.metadata
        if slug is not None:
            final_slug = validate_slug(slug)
        else:
            final_slug = slug_from_candidate(metadata.get('slug') or metadata.get('id'), filename or 'skill')
        namespace = f'user/{hasn_id}'
        skill_id = build_resource_id(namespace, final_slug)
        version = validate_version(str(metadata.get('version') or '1.0.0'))
        file_hash = hashlib.sha256(content).hexdigest()
        file_size = len(content)
        package_url, _, _ = await marketplace_storage_service.upload_skill_package(
            db=db,
            skill_id=skill_id,
            version=version,
            content=content,
        )
        icon_url = None
        if package.icon:
            icon_url = await marketplace_storage_service.upload_icon(
                db=db,
                item_type='skill',
                item_id=skill_id,
                content=package.icon.content,
                filename=package.icon.filename,
            )

        skill = await marketplace_skill_dao.get_by_namespace_slug_for_user(db, namespace, final_slug, user_id)
        tags, tags_en, tags_zh = _localized_tags_for_storage(metadata.get('tags', []))
        body = extract_skill_body(package.markdown)
        body_en, body_zh = raw_bilingual_body(None, body)
        source_language = 'zh' if body_zh is not None else 'en'
        files = json.dumps(package.files, ensure_ascii=False)
        if skill:
            skill.name = str(metadata.get('name'))
            skill.name_en = str(metadata.get('name'))
            skill.name_zh = str(metadata.get('name'))
            skill.description_en = str(metadata.get('description'))
            skill.description_zh = str(metadata.get('description'))
            skill.category = metadata.get('category')
            skill.tags = tags
            skill.tags_en = tags_en
            skill.tags_zh = tags_zh
            skill.icon_url = icon_url or skill.icon_url
            skill.emoji = metadata.get('emoji')
            skill.status = 'draft'
            skill.visibility = 'private'
            skill.requested_visibility = requested_visibility
            skill.is_private = True
            skill.source_type = 'user'
            skill.body_en = body_en
            skill.body_zh = body_zh
            skill.source_language = source_language
            skill.files = files
        else:
            skill = MarketplaceSkill(
                skill_id=skill_id,
                namespace=namespace,
                slug=final_slug,
                user_id=user_id,
                hasn_id=hasn_id,
                status='draft',
                visibility='private',
                requested_visibility=requested_visibility,
                name=str(metadata.get('name')),
                name_en=str(metadata.get('name')),
                name_zh=str(metadata.get('name')),
                description_en=str(metadata.get('description')),
                description_zh=str(metadata.get('description')),
                body_en=body_en,
                body_zh=body_zh,
                source_language=source_language,
                files=files,
                icon_url=icon_url,
                emoji=metadata.get('emoji'),
                author_id=user_id,
                author_name=metadata.get('author') or hasn_id,
                category=metadata.get('category'),
                tags=tags,
                tags_en=tags_en,
                tags_zh=tags_zh,
                source_type='user',
                pricing_type='free',
                price=Decimal(0),
                is_private=True,
                is_official=False,
                download_count=0,
            )
            db.add(skill)
            await db.flush()

        await db.execute(
            update(MarketplaceSkillVersion)
            .where(MarketplaceSkillVersion.skill_id == skill_id)
            .values(is_latest=False)
        )
        existing_version = await marketplace_skill_version_dao.get_by_skill_and_version(db, skill_id, version)
        if existing_version:
            existing_version.changelog = changelog
            existing_version.package_url = package_url
            existing_version.file_hash = file_hash
            existing_version.content_hash = package.content_hash
            existing_version.file_size = file_size
            existing_version.is_latest = True
        else:
            db.add(MarketplaceSkillVersion(
                skill_id=skill_id,
                version=version,
                changelog=changelog,
                package_url=package_url,
                file_hash=file_hash,
                content_hash=package.content_hash,
                file_size=file_size,
                is_latest=True,
            ))
        if commit:
            await db.commit()
            await db.refresh(skill)
        else:
            await db.flush()
        return skill

    @staticmethod
    async def submit_review(*, db: AsyncSession, resource_id: str, user_id: int) -> MarketplaceSkill:
        skill = await MarketplaceSkillService.get_by_resource_id_for_user(
            db=db,
            resource_id=resource_id,
            user_id=user_id,
        )
        if skill.status not in {'draft', 'rejected', 'unpublished'}:
            raise errors.RequestError(msg='当前状态不能提交审核')
        skill.status = 'pending_review'
        skill.visibility = 'private'
        skill.is_private = True
        await db.commit()
        await db.refresh(skill)
        return skill

    publish = submit_review

    @staticmethod
    async def approve(
        *,
        db: AsyncSession,
        resource_id: str,
        reviewer_id: int | None = None,
        review_note: str | None = None,
    ) -> MarketplaceSkill:
        namespace, slug = parse_resource_id(resource_id)
        skill = await marketplace_skill_dao.get_by_namespace_slug(db, namespace, slug)
        if not skill:
            raise errors.NotFoundError(msg='技能不存在')
        now = timezone.now()
        skill.status = 'published'
        skill.visibility = 'public'
        skill.is_private = False
        skill.reviewed_by = reviewer_id
        skill.reviewed_at = now
        skill.review_note = review_note
        skill.published_at = now
        await db.commit()
        await db.refresh(skill)
        return skill

    @staticmethod
    async def reject(
        *,
        db: AsyncSession,
        resource_id: str,
        reviewer_id: int | None = None,
        review_note: str | None = None,
    ) -> MarketplaceSkill:
        namespace, slug = parse_resource_id(resource_id)
        skill = await marketplace_skill_dao.get_by_namespace_slug(db, namespace, slug)
        if not skill:
            raise errors.NotFoundError(msg='技能不存在')
        skill.status = 'rejected'
        skill.visibility = 'private'
        skill.is_private = True
        skill.reviewed_by = reviewer_id
        skill.reviewed_at = timezone.now()
        skill.review_note = review_note
        await db.commit()
        await db.refresh(skill)
        return skill

    @staticmethod
    async def suspend(
        *,
        db: AsyncSession,
        resource_id: str,
        reviewer_id: int | None = None,
        suspend_reason: str | None = None,
    ) -> MarketplaceSkill:
        namespace, slug = parse_resource_id(resource_id)
        skill = await marketplace_skill_dao.get_by_namespace_slug(db, namespace, slug)
        if not skill:
            raise errors.NotFoundError(msg='技能不存在')
        now = timezone.now()
        skill.status = 'suspended'
        skill.visibility = 'private'
        skill.is_private = True
        skill.reviewed_by = reviewer_id
        skill.reviewed_at = now
        skill.suspended_at = now
        skill.suspend_reason = suspend_reason
        await db.commit()
        await db.refresh(skill)
        return skill

    @staticmethod
    async def unpublish(*, db: AsyncSession, resource_id: str, user_id: int) -> MarketplaceSkill:
        skill = await MarketplaceSkillService.get_by_resource_id_for_user(
            db=db,
            resource_id=resource_id,
            user_id=user_id,
        )
        skill.status = 'unpublished'
        skill.visibility = 'private'
        skill.is_private = True
        await db.commit()
        await db.refresh(skill)
        return skill

    @staticmethod
    async def update_user_skill(
        *,
        db: AsyncSession,
        resource_id: str,
        user_id: int,
        payload: dict[str, Any],
    ) -> MarketplaceSkill:
        skill = await MarketplaceSkillService.get_by_resource_id_for_user(
            db=db,
            resource_id=resource_id,
            user_id=user_id,
        )
        unknown = set(payload) - EDITABLE_FIELDS
        if unknown:
            raise errors.RequestError(msg=f'不支持更新字段: {", ".join(sorted(unknown))}')
        if 'name' in payload:
            skill.name = str(payload['name'])
            skill.name_en = str(payload['name'])
            skill.name_zh = str(payload['name'])
        if 'description' in payload:
            skill.description_en = str(payload['description'])
            skill.description_zh = str(payload['description'])
        if 'category' in payload:
            skill.category = payload['category']
        if 'tags' in payload:
            skill.tags, skill.tags_en, skill.tags_zh = _localized_tags_for_storage(payload['tags'])
        if 'emoji' in payload:
            skill.emoji = payload['emoji']
        if 'icon_url' in payload:
            skill.icon_url = payload['icon_url']
        if skill.status == 'published':
            skill.status = 'draft'
            skill.visibility = 'private'
            skill.is_private = True
        await db.commit()
        await db.refresh(skill)
        return skill

    @staticmethod
    async def delete_user_skill(*, db: AsyncSession, resource_id: str, user_id: int) -> None:
        skill = await MarketplaceSkillService.get_by_resource_id_for_user(
            db=db,
            resource_id=resource_id,
            user_id=user_id,
        )
        await db.delete(skill)
        await db.commit()

    @staticmethod
    async def admin_create(*, db: AsyncSession, obj: CreateMarketplaceSkillParam) -> MarketplaceSkill:
        namespace, slug = parse_resource_id(obj.skill_id)
        values = obj.model_dump()
        values['namespace'] = values.get('namespace') or namespace
        values['slug'] = values.get('slug') or slug
        skill = MarketplaceSkill(**values)
        db.add(skill)
        await db.commit()
        await db.refresh(skill)
        return skill

    @staticmethod
    async def admin_update(
        *,
        db: AsyncSession,
        resource_id: str,
        obj: UpdateMarketplaceSkillParam,
    ) -> MarketplaceSkill:
        skill = await MarketplaceSkillService.get_by_resource_id_admin(db=db, resource_id=resource_id)
        values = obj.model_dump()
        if values['skill_id'] != skill.skill_id:
            namespace, slug = parse_resource_id(values['skill_id'])
            values['namespace'] = values.get('namespace') or namespace
            values['slug'] = values.get('slug') or slug
        for field, value in values.items():
            setattr(skill, field, value)
        await db.commit()
        await db.refresh(skill)
        return skill

    @staticmethod
    async def admin_delete(*, db: AsyncSession, resource_id: str) -> None:
        skill = await MarketplaceSkillService.get_by_resource_id_admin(db=db, resource_id=resource_id)
        await db.delete(skill)
        await db.commit()

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateMarketplaceSkillParam) -> None:
        """
        创建技能市场技能

        :param db: 数据库会话
        :param obj: 创建技能市场技能参数
        :return:
        """
        await marketplace_skill_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateMarketplaceSkillParam) -> int:
        """
        更新技能市场技能

        :param db: 数据库会话
        :param pk: 技能市场技能 ID
        :param obj: 更新技能市场技能参数
        :return:
        """
        count = await marketplace_skill_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteMarketplaceSkillParam) -> int:
        """
        删除技能市场技能

        :param db: 数据库会话
        :param obj: 技能市场技能 ID 列表
        :return:
        """
        count = await marketplace_skill_dao.delete(db, obj.pks)
        return count


marketplace_skill_service: MarketplaceSkillService = MarketplaceSkillService()

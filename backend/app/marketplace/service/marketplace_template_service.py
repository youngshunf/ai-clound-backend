import hashlib

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_template import marketplace_template_dao
from backend.app.marketplace.crud.crud_marketplace_template_version import marketplace_template_version_dao
from backend.app.marketplace.model import MarketplaceTemplate, MarketplaceTemplateVersion
from backend.app.marketplace.schema.marketplace_template import (
    CreateMarketplaceTemplateParam,
    DeleteMarketplaceTemplateParam,
    UpdateMarketplaceTemplateParam,
)
from backend.app.marketplace.service.package_validation import parse_template_package
from backend.app.marketplace.service.resource_id import (
    build_resource_id,
    parse_resource_id,
    slug_from_candidate,
    validate_slug,
    validate_version,
)
from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.common.exception import errors
from backend.common.pagination import paging_data
from backend.utils.timezone import timezone

EDITABLE_FIELDS = {
    'name',
    'description',
    'category',
    'tags',
    'emoji',
    'icon_url',
    'skill_dependencies',
    'sop_dependencies',
}


def _normalize_tags_for_storage(tags: Any) -> str | None:
    if tags is None:
        return None
    if isinstance(tags, str):
        values = [item.strip() for item in tags.split(',')]
    elif isinstance(tags, list):
        values = [str(item).strip() for item in tags]
    else:
        values = [str(tags).strip()]
    return ','.join(value for value in values if value)


def _normalize_dependencies_for_storage(deps: Any) -> str | None:
    if deps is None:
        return None
    if isinstance(deps, str):
        values = [item.strip() for item in deps.split(',')]
    elif isinstance(deps, list):
        values = [str(item).strip() for item in deps]
    else:
        values = [str(deps).strip()]
    return ','.join(value for value in values if value)


class MarketplaceTemplateService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> MarketplaceTemplate:
        """
        获取技能市场模板表（Agent模板/技能包/SOP包）

        :param db: 数据库会话
        :param pk: 技能市场模板表（Agent模板/技能包/SOP包） ID
        :return:
        """
        marketplace_template = await marketplace_template_dao.get(db, pk)
        if not marketplace_template:
            raise errors.NotFoundError(msg='技能市场模板表（Agent模板/技能包/SOP包）不存在')
        return marketplace_template

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取技能市场模板表（Agent模板/技能包/SOP包）列表

        :param db: 数据库会话
        :return:
        """
        marketplace_template_select = await marketplace_template_dao.get_select()
        return await paging_data(db, marketplace_template_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[MarketplaceTemplate]:
        """
        获取所有技能市场模板表（Agent模板/技能包/SOP包）

        :param db: 数据库会话
        :return:
        """
        marketplace_template_list = await marketplace_template_dao.get_all(db)
        return marketplace_template_list

    @staticmethod
    def format_template(template: MarketplaceTemplate) -> dict[str, Any]:
        return {
            'id': template.id,
            'template_id': template.template_id,
            'namespace': template.namespace,
            'slug': template.slug,
            'user_id': template.user_id,
            'hasn_id': template.hasn_id,
            'status': template.status,
            'visibility': template.visibility,
            'template_type': template.template_type,
            'name': template.name,
            'name_en': template.name_en,
            'name_zh': template.name_zh,
            'name_pool': [n.strip() for n in (template.name_pool or '').split(',') if n.strip()],
            'description': template.description,
            'description_en': template.description_en,
            'description_zh': template.description_zh,
            'icon_url': template.icon_url,
            'emoji': template.emoji,
            'author_name': template.author_name,
            'category': template.category,
            'tags': template.tags,
            'source_type': template.source_type,
            'pricing_type': template.pricing_type,
            'price': float(template.price) if template.price is not None else 0,
            'download_count': template.download_count,
            'is_official': template.is_official,
            'skill_dependencies': template.skill_dependencies,
            'sop_dependencies': template.sop_dependencies,
            'reviewed_by': template.reviewed_by,
            'reviewed_at': template.reviewed_at.isoformat() if template.reviewed_at else None,
            'review_note': template.review_note,
            'published_at': template.published_at.isoformat() if template.published_at else None,
            'created_time': template.created_time.isoformat() if template.created_time else None,
            'updated_time': template.updated_time.isoformat() if template.updated_time else None,
        }

    @staticmethod
    async def get_by_resource_id_public(*, db: AsyncSession, resource_id: str) -> MarketplaceTemplate:
        namespace, slug = parse_resource_id(resource_id)
        template = await marketplace_template_dao.get_by_namespace_slug_public(db, namespace, slug)
        if not template:
            raise errors.NotFoundError(msg='模板不存在')
        return template

    @staticmethod
    async def get_by_resource_id_for_user(*, db: AsyncSession, resource_id: str, user_id: int) -> MarketplaceTemplate:
        namespace, slug = parse_resource_id(resource_id)
        template = await marketplace_template_dao.get_by_namespace_slug_for_user(db, namespace, slug, user_id)
        if not template:
            raise errors.NotFoundError(msg='模板不存在')
        return template

    @staticmethod
    async def get_by_resource_id_admin(*, db: AsyncSession, resource_id: str) -> MarketplaceTemplate:
        namespace, slug = parse_resource_id(resource_id)
        template = await marketplace_template_dao.get_by_namespace_slug(db, namespace, slug)
        if not template:
            raise errors.NotFoundError(msg='模板不存在')
        return template

    @staticmethod
    async def list_user_templates(*, db: AsyncSession, user_id: int) -> list[dict[str, Any]]:
        templates = await marketplace_template_dao.get_by_user(db, user_id)
        # 技能包（skill_pack）在「我的发布」有独立 tab（走 /skill-packs?mine=true），
        # 不混进「模板」tab（实施/92-UI 修泄漏）。
        return [
            MarketplaceTemplateService.format_template(template)
            for template in templates
            if template.template_type != 'skill_pack'
        ]

    @staticmethod
    async def upload_user_template(
        *,
        db: AsyncSession,
        user_id: int,
        hasn_id: str,
        content: bytes,
        filename: str | None = None,
        slug: str | None = None,
        changelog: str | None = None,
    ) -> MarketplaceTemplate:
        if not hasn_id:
            raise errors.AuthorizationError(msg='用户未注册 HASN 身份')
        package = parse_template_package(content)
        metadata = package.metadata
        if slug is not None:
            final_slug = validate_slug(slug)
        else:
            final_slug = slug_from_candidate(
                metadata.get('slug') or metadata.get('id') or metadata.get('name'),
                filename or 'template',
            )
        namespace = f'user/{hasn_id}'
        template_id = build_resource_id(namespace, final_slug)
        version = validate_version(str(metadata.get('version') or '1.0.0'))
        file_hash = hashlib.sha256(content).hexdigest()
        file_size = len(content)
        package_url, _, _ = await marketplace_storage_service.upload_template_package(
            db=db,
            template_id=template_id,
            version=version,
            content=content,
        )
        icon_url = None
        if package.icon:
            icon_url = await marketplace_storage_service.upload_icon(
                db=db,
                item_type='template',
                item_id=template_id,
                content=package.icon.content,
                filename=package.icon.filename,
            )
        tags = ','.join(metadata.get('tags', []))
        skill_deps = metadata.get('skills') or metadata.get('skill_dependencies') or []
        sop_deps = metadata.get('sops') or metadata.get('sop_dependencies') or []
        skill_deps_str = ','.join(skill_deps) if isinstance(skill_deps, list) else str(skill_deps)
        sop_deps_str = ','.join(sop_deps) if isinstance(sop_deps, list) else str(sop_deps)
        template = await marketplace_template_dao.get_by_namespace_slug_for_user(db, namespace, final_slug, user_id)
        if template:
            template.name = str(metadata.get('display_name') or metadata.get('name'))
            template.name_en = template.name
            template.name_zh = template.name
            template.description = str(metadata.get('description'))
            template.description_en = template.description
            template.description_zh = template.description
            template.category = metadata.get('category')
            template.tags = tags
            template.icon_url = icon_url or template.icon_url
            template.emoji = metadata.get('emoji')
            template.skill_dependencies = skill_deps_str
            template.sop_dependencies = sop_deps_str
            template.status = 'draft'
            template.visibility = 'private'
            template.is_private = True
            template.source_type = 'user'
        else:
            template = MarketplaceTemplate(
                template_id=template_id,
                namespace=namespace,
                slug=final_slug,
                user_id=user_id,
                hasn_id=hasn_id,
                status='draft',
                visibility='private',
                template_type=metadata.get('template_type') or 'agent_template',
                name=str(metadata.get('display_name') or metadata.get('name')),
                name_en=str(metadata.get('display_name') or metadata.get('name')),
                name_zh=str(metadata.get('display_name') or metadata.get('name')),
                description=str(metadata.get('description')),
                description_en=str(metadata.get('description')),
                description_zh=str(metadata.get('description')),
                source_language='en',
                icon_url=icon_url,
                emoji=metadata.get('emoji'),
                author_id=user_id,
                author_name=metadata.get('author') or hasn_id,
                pricing_type='free',
                price=Decimal(0),
                is_private=True,
                is_official=False,
                download_count=0,
                category=metadata.get('category'),
                tags=tags,
                source_type='user',
                skill_dependencies=skill_deps_str,
                sop_dependencies=sop_deps_str,
            )
            db.add(template)
            await db.flush()

        await db.execute(
            update(MarketplaceTemplateVersion)
            .where(MarketplaceTemplateVersion.template_id == template_id)
            .values(is_latest=False)
        )
        existing_version = await marketplace_template_version_dao.get_by_template_and_version(db, template_id, version)
        skill_deps_versioned = dict.fromkeys(skill_deps, '*') if isinstance(skill_deps, list) else None
        if existing_version:
            existing_version.changelog = changelog
            existing_version.skill_dependencies_versioned = skill_deps_versioned
            existing_version.package_url = package_url
            existing_version.file_hash = file_hash
            existing_version.file_size = file_size
            existing_version.is_latest = True
        else:
            db.add(MarketplaceTemplateVersion(
                template_id=template_id,
                version=version,
                changelog=changelog,
                skill_dependencies_versioned=skill_deps_versioned,
                package_url=package_url,
                file_hash=file_hash,
                file_size=file_size,
                is_latest=True,
            ))
        await db.commit()
        await db.refresh(template)
        return template

    @staticmethod
    async def submit_review(*, db: AsyncSession, resource_id: str, user_id: int) -> MarketplaceTemplate:
        template = await MarketplaceTemplateService.get_by_resource_id_for_user(
            db=db,
            resource_id=resource_id,
            user_id=user_id,
        )
        if template.status not in {'draft', 'rejected', 'unpublished'}:
            raise errors.RequestError(msg='当前状态不能提交审核')
        template.status = 'pending_review'
        template.visibility = 'private'
        template.is_private = True
        await db.commit()
        await db.refresh(template)
        return template

    publish = submit_review

    @staticmethod
    async def approve(
        *,
        db: AsyncSession,
        resource_id: str,
        reviewer_id: int | None = None,
        review_note: str | None = None,
    ) -> MarketplaceTemplate:
        namespace, slug = parse_resource_id(resource_id)
        template = await marketplace_template_dao.get_by_namespace_slug(db, namespace, slug)
        if not template:
            raise errors.NotFoundError(msg='模板不存在')
        now = timezone.now()
        template.status = 'published'
        template.visibility = 'public'
        template.is_private = False
        template.reviewed_by = reviewer_id
        template.reviewed_at = now
        template.review_note = review_note
        template.published_at = now
        await db.commit()
        await db.refresh(template)
        return template

    @staticmethod
    async def reject(
        *,
        db: AsyncSession,
        resource_id: str,
        reviewer_id: int | None = None,
        review_note: str | None = None,
    ) -> MarketplaceTemplate:
        namespace, slug = parse_resource_id(resource_id)
        template = await marketplace_template_dao.get_by_namespace_slug(db, namespace, slug)
        if not template:
            raise errors.NotFoundError(msg='模板不存在')
        template.status = 'rejected'
        template.visibility = 'private'
        template.is_private = True
        template.reviewed_by = reviewer_id
        template.reviewed_at = timezone.now()
        template.review_note = review_note
        await db.commit()
        await db.refresh(template)
        return template

    @staticmethod
    async def suspend(
        *,
        db: AsyncSession,
        resource_id: str,
        reviewer_id: int | None = None,
        suspend_reason: str | None = None,
    ) -> MarketplaceTemplate:
        namespace, slug = parse_resource_id(resource_id)
        template = await marketplace_template_dao.get_by_namespace_slug(db, namespace, slug)
        if not template:
            raise errors.NotFoundError(msg='模板不存在')
        now = timezone.now()
        template.status = 'suspended'
        template.visibility = 'private'
        template.is_private = True
        template.reviewed_by = reviewer_id
        template.reviewed_at = now
        template.suspended_at = now
        template.suspend_reason = suspend_reason
        await db.commit()
        await db.refresh(template)
        return template

    @staticmethod
    async def unpublish(*, db: AsyncSession, resource_id: str, user_id: int) -> MarketplaceTemplate:
        template = await MarketplaceTemplateService.get_by_resource_id_for_user(
            db=db,
            resource_id=resource_id,
            user_id=user_id,
        )
        template.status = 'unpublished'
        template.visibility = 'private'
        template.is_private = True
        await db.commit()
        await db.refresh(template)
        return template

    @staticmethod
    async def update_user_template(  # noqa: C901
        *,
        db: AsyncSession,
        resource_id: str,
        user_id: int,
        payload: dict[str, Any],
    ) -> MarketplaceTemplate:
        template = await MarketplaceTemplateService.get_by_resource_id_for_user(
            db=db,
            resource_id=resource_id,
            user_id=user_id,
        )
        unknown = set(payload) - EDITABLE_FIELDS
        if unknown:
            raise errors.RequestError(msg=f'不支持更新字段: {", ".join(sorted(unknown))}')
        if 'name' in payload:
            template.name = str(payload['name'])
            template.name_en = template.name
            template.name_zh = template.name
        if 'description' in payload:
            template.description = str(payload['description'])
            template.description_en = template.description
            template.description_zh = template.description
        if 'category' in payload:
            template.category = payload['category']
        if 'tags' in payload:
            template.tags = _normalize_tags_for_storage(payload['tags'])
        if 'skill_dependencies' in payload:
            template.skill_dependencies = _normalize_dependencies_for_storage(payload['skill_dependencies'])
        if 'sop_dependencies' in payload:
            template.sop_dependencies = _normalize_dependencies_for_storage(payload['sop_dependencies'])
        if 'emoji' in payload:
            template.emoji = payload['emoji']
        if 'icon_url' in payload:
            template.icon_url = payload['icon_url']
        if template.status == 'published':
            template.status = 'draft'
            template.visibility = 'private'
            template.is_private = True
        await db.commit()
        await db.refresh(template)
        return template

    @staticmethod
    async def delete_user_template(*, db: AsyncSession, resource_id: str, user_id: int) -> None:
        template = await MarketplaceTemplateService.get_by_resource_id_for_user(
            db=db,
            resource_id=resource_id,
            user_id=user_id,
        )
        await db.delete(template)
        await db.commit()

    @staticmethod
    async def admin_create(*, db: AsyncSession, obj: CreateMarketplaceTemplateParam) -> MarketplaceTemplate:
        namespace, slug = parse_resource_id(obj.template_id)
        values = obj.model_dump()
        values['namespace'] = values.get('namespace') or namespace
        values['slug'] = values.get('slug') or slug
        template = MarketplaceTemplate(**values)
        db.add(template)
        await db.commit()
        await db.refresh(template)
        return template

    @staticmethod
    async def admin_update(
        *,
        db: AsyncSession,
        resource_id: str,
        obj: UpdateMarketplaceTemplateParam,
    ) -> MarketplaceTemplate:
        template = await MarketplaceTemplateService.get_by_resource_id_admin(db=db, resource_id=resource_id)
        values = obj.model_dump()
        if values['template_id'] != template.template_id:
            namespace, slug = parse_resource_id(values['template_id'])
            values['namespace'] = values.get('namespace') or namespace
            values['slug'] = values.get('slug') or slug
        for field, value in values.items():
            setattr(template, field, value)
        await db.commit()
        await db.refresh(template)
        return template

    @staticmethod
    async def admin_delete(*, db: AsyncSession, resource_id: str) -> None:
        template = await MarketplaceTemplateService.get_by_resource_id_admin(db=db, resource_id=resource_id)
        await db.delete(template)
        await db.commit()

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateMarketplaceTemplateParam) -> None:
        """
        创建技能市场模板表（Agent模板/技能包/SOP包）

        :param db: 数据库会话
        :param obj: 创建技能市场模板表（Agent模板/技能包/SOP包）参数
        :return:
        """
        await marketplace_template_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateMarketplaceTemplateParam) -> int:
        """
        更新技能市场模板表（Agent模板/技能包/SOP包）

        :param db: 数据库会话
        :param pk: 技能市场模板表（Agent模板/技能包/SOP包） ID
        :param obj: 更新技能市场模板表（Agent模板/技能包/SOP包）参数
        :return:
        """
        count = await marketplace_template_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteMarketplaceTemplateParam) -> int:
        """
        删除技能市场模板表（Agent模板/技能包/SOP包）

        :param db: 数据库会话
        :param obj: 技能市场模板表（Agent模板/技能包/SOP包） ID 列表
        :return:
        """
        count = await marketplace_template_dao.delete(db, obj.pks)
        return count


marketplace_template_service: MarketplaceTemplateService = MarketplaceTemplateService()

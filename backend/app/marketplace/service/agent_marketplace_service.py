"""技能市场 Agent JWT 权威查询服务（DOC15-95 M1-1）。"""

from __future__ import annotations

import json

from typing import Any, Literal

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.model.marketplace_skill import MarketplaceSkill
from backend.app.marketplace.model.marketplace_skill_version import MarketplaceSkillVersion
from backend.app.marketplace.model.marketplace_template import MarketplaceTemplate
from backend.app.marketplace.model.marketplace_template_version import MarketplaceTemplateVersion
from backend.app.marketplace.service import skill_pack_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors

ResourceKind = Literal['skill', 'template', 'skill_pack']


def _parse_cursor(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        offset = int(cursor)
    except ValueError as exc:
        raise errors.RequestError(code=422, msg='marketplace_cursor_invalid') from exc
    if offset < 0:
        raise errors.RequestError(code=422, msg='marketplace_cursor_invalid')
    return offset


def _next_cursor(*, offset: int, limit: int, total: int) -> str | None:
    candidate = offset + limit
    return str(candidate) if candidate < total else None


def _skill_acl(agent: AgentTokenPayload) -> Any:
    return sa.or_(
        sa.and_(MarketplaceSkill.status == 'published', MarketplaceSkill.visibility == 'public'),
        MarketplaceSkill.user_id == agent.owner_user_id,
        MarketplaceSkill.author_id == agent.owner_user_id,
        MarketplaceSkill.hasn_id == agent.owner_hasn_id,
    )


def _template_acl(agent: AgentTokenPayload) -> Any:
    return sa.or_(
        sa.and_(MarketplaceTemplate.status == 'published', MarketplaceTemplate.visibility == 'public'),
        MarketplaceTemplate.user_id == agent.owner_user_id,
        MarketplaceTemplate.author_id == agent.owner_user_id,
        MarketplaceTemplate.hasn_id == agent.owner_hasn_id,
    )


def _language_code(language: str | None) -> str | None:
    if not language:
        return None
    return language.split('-', 1)[0].lower()


def _safe_files(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [
        {'path': str(item['path']), 'size': item.get('size')}
        for item in value
        if isinstance(item, dict) and item.get('path')
    ]


def _localized_skill(skill: MarketplaceSkill, language: str | None) -> tuple[str, str | None]:
    code = _language_code(language) or 'zh'
    if code == 'en':
        return (
            skill.name_en or skill.name_zh or skill.name,
            skill.description_en or skill.description_zh,
        )
    return (
        skill.name_zh or skill.name_en or skill.name,
        skill.description_zh or skill.description_en,
    )


def _skill_item(skill: MarketplaceSkill, language: str | None) -> dict[str, Any]:
    name, description = _localized_skill(skill, language)
    return {
        'skill_id': skill.skill_id,
        'resource_uri': f'hasn://marketplace/skills/{skill.skill_id}',
        'namespace': skill.namespace,
        'slug': skill.slug,
        'name': name,
        'description': description,
        'source_type': skill.source_type,
        'status': skill.status,
        'visibility': skill.visibility,
        'category': skill.category,
        'tags': skill.tags,
        'is_common': skill.is_common,
        'updated_time': (skill.updated_time or skill.created_time).isoformat()
        if (skill.updated_time or skill.created_time)
        else None,
    }


def _template_item(template: MarketplaceTemplate, kind: ResourceKind) -> dict[str, Any]:
    identifier_key = 'package_id' if kind == 'skill_pack' else 'template_id'
    uri_kind = 'skill-packs' if kind == 'skill_pack' else 'templates'
    return {
        identifier_key: template.template_id,
        'resource_uri': f'hasn://marketplace/{uri_kind}/{template.template_id}',
        'namespace': template.namespace,
        'slug': template.slug,
        'name': template.name or template.name_zh or template.name_en,
        'description': template.description or template.description_zh or template.description_en,
        'source_type': template.source_type,
        'status': template.status,
        'visibility': template.visibility,
        'category': template.category,
        'tags': template.tags,
        'template_type': template.template_type,
        'updated_time': (template.updated_time or template.created_time).isoformat()
        if (template.updated_time or template.created_time)
        else None,
    }


class AgentMarketplaceService:
    """按 Agent 凭证派生 ACL 的市场查询服务。"""

    async def search_skills(
        self,
        db: AsyncSession,
        *,
        agent: AgentTokenPayload,
        query: str | None,
        category: str | None,
        tags: list[str] | None,
        source_type: str | None,
        namespace: str | None,
        language: str | None,
        sort: str,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        stmt = sa.select(MarketplaceSkill).where(_skill_acl(agent))
        if query:
            pattern = f'%{query}%'
            stmt = stmt.where(
                sa.or_(
                    MarketplaceSkill.skill_id.ilike(pattern),
                    MarketplaceSkill.name.ilike(pattern),
                    MarketplaceSkill.name_zh.ilike(pattern),
                    MarketplaceSkill.name_en.ilike(pattern),
                    MarketplaceSkill.description_zh.ilike(pattern),
                    MarketplaceSkill.description_en.ilike(pattern),
                )
            )
        if category:
            stmt = stmt.where(MarketplaceSkill.category == category)
        if source_type:
            stmt = stmt.where(MarketplaceSkill.source_type == source_type)
        if namespace:
            stmt = stmt.where(MarketplaceSkill.namespace == namespace)
        for tag in tags or []:
            stmt = stmt.where(
                sa.or_(
                    MarketplaceSkill.tags.ilike(f'%{tag}%'),
                    MarketplaceSkill.tags_zh.ilike(f'%{tag}%'),
                    MarketplaceSkill.tags_en.ilike(f'%{tag}%'),
                )
            )
        language_code = _language_code(language)
        if language_code == 'zh':
            stmt = stmt.where(
                sa.or_(MarketplaceSkill.name_zh.is_not(None), MarketplaceSkill.source_language == 'zh')
            )
        elif language_code == 'en':
            stmt = stmt.where(
                sa.or_(MarketplaceSkill.name_en.is_not(None), MarketplaceSkill.source_language == 'en')
            )

        total = int((await db.execute(sa.select(sa.func.count()).select_from(stmt.subquery()))).scalar() or 0)
        if sort == 'downloads':
            stmt = stmt.order_by(MarketplaceSkill.download_count.desc(), MarketplaceSkill.id.desc())
        elif sort == 'updated':
            stmt = stmt.order_by(
                sa.func.coalesce(MarketplaceSkill.updated_time, MarketplaceSkill.created_time).desc(),
                MarketplaceSkill.id.desc(),
            )
        else:
            stmt = stmt.order_by(
                (MarketplaceSkill.download_count + MarketplaceSkill.star_count * 10).desc(),
                MarketplaceSkill.id.desc(),
            )
        offset = _parse_cursor(cursor)
        rows = list((await db.execute(stmt.offset(offset).limit(limit))).scalars().all())
        items = [_skill_item(row, language) for row in rows]

        if rows:
            versions = (
                await db.execute(
                    sa.select(MarketplaceSkillVersion).where(
                        MarketplaceSkillVersion.skill_id.in_([row.skill_id for row in rows]),
                        MarketplaceSkillVersion.is_latest.is_(True),
                    )
                )
            ).scalars().all()
            by_skill = {version.skill_id: version for version in versions}
            for item in items:
                version = by_skill.get(str(item['skill_id']))
                if version is not None:
                    item['latest_version'] = version.version
                    item['content_hash'] = version.content_hash or version.file_hash
                    item['file_hash'] = version.file_hash

        return {
            'items': items,
            'next_cursor': _next_cursor(offset=offset, limit=limit, total=total),
            'total': total,
        }

    async def get_skill(
        self,
        db: AsyncSession,
        *,
        agent: AgentTokenPayload,
        resource_id: str,
        language: str | None,
        version: str | None,
    ) -> dict[str, Any]:
        skill = await self._resolve_skill(db, agent=agent, resource_id=resource_id)
        item = _skill_item(skill, language)
        version_stmt = sa.select(MarketplaceSkillVersion).where(
            MarketplaceSkillVersion.skill_id == skill.skill_id
        )
        if version:
            version_stmt = version_stmt.where(MarketplaceSkillVersion.version == version)
        versions = (
            await db.execute(version_stmt.order_by(MarketplaceSkillVersion.published_at.desc()))
        ).scalars().all()
        if version and not versions:
            raise errors.NotFoundError(msg='marketplace_skill_version_not_found')
        item['versions'] = [
            {
                'version': row.version,
                'content_hash': row.content_hash or row.file_hash,
                'file_hash': row.file_hash,
                'file_size': row.file_size,
                'is_latest': row.is_latest,
            }
            for row in versions
        ]
        latest = next((row for row in versions if row.is_latest), versions[0] if versions else None)
        if latest is not None:
            item['latest_version'] = latest.version
            item['content_hash'] = latest.content_hash or latest.file_hash
            item['file_hash'] = latest.file_hash
        item['files'] = _safe_files(skill.files)
        return item

    async def search_templates(
        self,
        db: AsyncSession,
        *,
        agent: AgentTokenPayload,
        kind: Literal['template', 'skill_pack'],
        query: str | None,
        category: str | None,
        tags: list[str] | None,
        source_type: str | None,
        namespace: str | None,
        language: str | None,
        sort: str,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        template_type = 'skill_pack' if kind == 'skill_pack' else 'agent_template'
        stmt = sa.select(MarketplaceTemplate).where(
            _template_acl(agent), MarketplaceTemplate.template_type == template_type
        )
        if query:
            pattern = f'%{query}%'
            stmt = stmt.where(
                sa.or_(
                    MarketplaceTemplate.template_id.ilike(pattern),
                    MarketplaceTemplate.name.ilike(pattern),
                    MarketplaceTemplate.name_zh.ilike(pattern),
                    MarketplaceTemplate.name_en.ilike(pattern),
                    MarketplaceTemplate.description.ilike(pattern),
                    MarketplaceTemplate.description_zh.ilike(pattern),
                    MarketplaceTemplate.description_en.ilike(pattern),
                )
            )
        if category:
            stmt = stmt.where(MarketplaceTemplate.category == category)
        if source_type:
            stmt = stmt.where(MarketplaceTemplate.source_type == source_type)
        if namespace:
            stmt = stmt.where(MarketplaceTemplate.namespace == namespace)
        for tag in tags or []:
            stmt = stmt.where(MarketplaceTemplate.tags.ilike(f'%{tag}%'))
        language_code = _language_code(language)
        if language_code == 'zh':
            stmt = stmt.where(
                sa.or_(MarketplaceTemplate.name_zh.is_not(None), MarketplaceTemplate.source_language == 'zh')
            )
        elif language_code == 'en':
            stmt = stmt.where(
                sa.or_(MarketplaceTemplate.name_en.is_not(None), MarketplaceTemplate.source_language == 'en')
            )

        total = int((await db.execute(sa.select(sa.func.count()).select_from(stmt.subquery()))).scalar() or 0)
        if sort == 'updated':
            stmt = stmt.order_by(
                sa.func.coalesce(MarketplaceTemplate.updated_time, MarketplaceTemplate.created_time).desc(),
                MarketplaceTemplate.id.desc(),
            )
        else:
            stmt = stmt.order_by(MarketplaceTemplate.download_count.desc(), MarketplaceTemplate.id.desc())
        offset = _parse_cursor(cursor)
        rows = list((await db.execute(stmt.offset(offset).limit(limit))).scalars().all())
        items = [_template_item(row, kind) for row in rows]
        await self._attach_template_versions(db, items=items, rows=rows, kind=kind)
        return {
            'items': items,
            'next_cursor': _next_cursor(offset=offset, limit=limit, total=total),
            'total': total,
        }

    async def get_template(
        self,
        db: AsyncSession,
        *,
        agent: AgentTokenPayload,
        kind: Literal['template', 'skill_pack'],
        resource_id: str,
        version: str | None,
    ) -> dict[str, Any]:
        template = await self._resolve_template(
            db,
            agent=agent,
            kind=kind,
            resource_id=resource_id,
        )
        item = _template_item(template, kind)
        stmt = sa.select(MarketplaceTemplateVersion).where(
            MarketplaceTemplateVersion.template_id == template.template_id
        )
        if version:
            stmt = stmt.where(MarketplaceTemplateVersion.version == version)
        versions = (
            await db.execute(stmt.order_by(MarketplaceTemplateVersion.published_at.desc()))
        ).scalars().all()
        if version and not versions:
            raise errors.NotFoundError(msg='marketplace_template_version_not_found')
        item['versions'] = [self._template_version_payload(row, kind=kind) for row in versions]
        selected = versions[0] if version and versions else next(
            (row for row in versions if row.is_latest), versions[0] if versions else None
        )
        if selected is not None:
            item.update(self._template_version_payload(selected, kind=kind))
        return item

    async def _resolve_skill(
        self,
        db: AsyncSession,
        *,
        agent: AgentTokenPayload,
        resource_id: str,
    ) -> MarketplaceSkill:
        stmt = sa.select(MarketplaceSkill).where(_skill_acl(agent))
        if '/' in resource_id:
            stmt = stmt.where(MarketplaceSkill.skill_id == resource_id)
        else:
            stmt = stmt.where(MarketplaceSkill.slug == resource_id)
        matches = (await db.execute(stmt.limit(2))).scalars().all()
        if not matches:
            raise errors.NotFoundError(msg='marketplace_skill_not_found')
        if len(matches) > 1:
            raise errors.ConflictError(
                msg='marketplace_skill_id_ambiguous',
                data={'candidates': [row.skill_id for row in matches]},
            )
        return matches[0]

    async def _resolve_template(
        self,
        db: AsyncSession,
        *,
        agent: AgentTokenPayload,
        kind: Literal['template', 'skill_pack'],
        resource_id: str,
    ) -> MarketplaceTemplate:
        template_type = 'skill_pack' if kind == 'skill_pack' else 'agent_template'
        stmt = sa.select(MarketplaceTemplate).where(
            _template_acl(agent), MarketplaceTemplate.template_type == template_type
        )
        if '/' in resource_id:
            stmt = stmt.where(MarketplaceTemplate.template_id == resource_id)
        else:
            stmt = stmt.where(MarketplaceTemplate.slug == resource_id)
        matches = (await db.execute(stmt.limit(2))).scalars().all()
        if not matches:
            raise errors.NotFoundError(msg=f'marketplace_{kind}_not_found')
        if len(matches) > 1:
            raise errors.ConflictError(
                msg=f'marketplace_{kind}_id_ambiguous',
                data={'candidates': [row.template_id for row in matches]},
            )
        return matches[0]

    async def _attach_template_versions(
        self,
        db: AsyncSession,
        *,
        items: list[dict[str, Any]],
        rows: list[MarketplaceTemplate],
        kind: Literal['template', 'skill_pack'],
    ) -> None:
        if not rows:
            return
        versions = (
            await db.execute(
                sa.select(MarketplaceTemplateVersion).where(
                    MarketplaceTemplateVersion.template_id.in_([row.template_id for row in rows]),
                    MarketplaceTemplateVersion.is_latest.is_(True),
                )
            )
        ).scalars().all()
        by_template = {version.template_id: version for version in versions}
        identifier_key = 'package_id' if kind == 'skill_pack' else 'template_id'
        for item in items:
            version = by_template.get(str(item[identifier_key]))
            if version is not None:
                item.update(self._template_version_payload(version, kind=kind))

    @staticmethod
    def _template_version_payload(
        version: MarketplaceTemplateVersion,
        *,
        kind: Literal['template', 'skill_pack'],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            'version': version.version,
            'content_hash': version.content_hash or version.file_hash,
            'file_hash': version.file_hash,
            'file_size': version.file_size,
            'is_latest': version.is_latest,
        }
        if kind == 'skill_pack':
            hermes_yaml = version.hermes_yaml or ''
            payload.update(
                {
                    'bundle_slug': version.bundle_slug,
                    'command_key': version.command_key,
                    'member_skill_ids': skill_pack_service.member_skill_ids(hermes_yaml),
                }
            )
        return payload


agent_marketplace_service = AgentMarketplaceService()

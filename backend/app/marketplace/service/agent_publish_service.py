"""Agent JWT 技能市场资产发布服务。"""

from __future__ import annotations

import hashlib
import logging
import re

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.service.hasn_asset_service import HasnAssetService
from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
from backend.app.mcp.artifact_registration import register_app_resource_artifact
from backend.app.marketplace.model import (
    MarketplaceAgentPublishRequest,
    MarketplaceTemplate,
    MarketplaceTemplateVersion,
)
from backend.app.marketplace.schema.agent_marketplace import AgentMarketplacePublishRequest
from backend.app.marketplace.schema.skill_pack import SkillPackCreateRequest
from backend.app.marketplace.service import skill_pack_service
from backend.app.marketplace.service.marketplace_agent_publish_request_service import (
    marketplace_agent_publish_request_service,
)
from backend.app.marketplace.service.marketplace_skill_service import marketplace_skill_service
from backend.app.marketplace.service.marketplace_template_service import marketplace_template_service
from backend.app.marketplace.service.package_validation import (
    SkillPackPackage,
    SkillPackage,
    TemplatePackage,
    parse_skill_pack_package,
    parse_skill_package,
    parse_template_package,
)
from backend.app.marketplace.service.resource_id import validate_version
from backend.app.marketplace.storage.s3_storage import marketplace_storage_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.plugin.s3.service.storage_service import StorageService

log = logging.getLogger(__name__)

ResourceKind = Literal['skill', 'template', 'skill_pack']
ParsedPackage = SkillPackage | TemplatePackage | SkillPackPackage
_ASSET_URI = re.compile(r'\Ahasn://asset/(?P<asset_id>ast_[0-9a-f]{32})\Z')
_ARTIFACT_KINDS = {
    'skill': 'marketplace.skill',
    'template': 'marketplace.template',
    'skill_pack': 'marketplace.skill_pack',
}
_SOURCE_TOOLS = {
    'skill': 'hasn.marketplace.publish_skill',
    'template': 'hasn.marketplace.publish_template',
    'skill_pack': 'hasn.marketplace.publish_skill_pack',
}


@dataclass(frozen=True, slots=True)
class PublicationSource:
    content: bytes
    file_hash: str
    package: ParsedPackage
    filename: str | None


class AgentPublishService:
    """从 Owner 私有资产创建市场草稿，并提供可回放的提审状态。"""

    async def publish(
        self,
        db: AsyncSession,
        *,
        identity: AgentTokenPayload,
        resource_kind: ResourceKind,
        payload: AgentMarketplacePublishRequest,
        idempotency_key: str,
        work_session_id: str | None,
    ) -> dict:
        source = await self._read_and_parse_source(
            db,
            owner_hasn_id=identity.owner_hasn_id,
            resource_kind=resource_kind,
            asset_uri=payload.asset_uri,
        )
        existing = await marketplace_agent_publish_request_service.lock_and_get(
            db,
            agent_hasn_id=identity.agent_hasn_id,
            resource_kind=resource_kind,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            marketplace_agent_publish_request_service.require_same_content(
                existing,
                content_hash=source.package.content_hash,
            )
            if existing.result is None:
                raise errors.ConflictError(msg='首次发布仍在处理中，请稍后使用同一幂等键重试')
            result = dict(existing.result)
            await db.commit()
            if result.get('review_submission', {}).get('status') == 'pending':
                return await self._complete_review(
                    db,
                    identity=identity,
                    resource_kind=resource_kind,
                    request_id=existing.id,
                    result=result,
                    work_session_id=existing.work_session_id,
                    idempotency_key=existing.idempotency_key,
                )
            return result

        request = await marketplace_agent_publish_request_service.create(
            db,
            agent_hasn_id=identity.agent_hasn_id,
            owner_hasn_id=identity.owner_hasn_id,
            resource_kind=resource_kind,
            idempotency_key=idempotency_key,
            asset_uri=payload.asset_uri,
            content_hash=source.package.content_hash,
            file_hash=source.file_hash,
            work_session_id=work_session_id,
        )
        if resource_kind == 'skill':
            result = await self._create_skill(db, identity=identity, payload=payload, source=source)
        elif resource_kind == 'template':
            result = await self._create_template(db, identity=identity, payload=payload, source=source)
        else:
            result = await self._create_skill_pack(db, identity=identity, payload=payload, source=source)

        result.update(
            {
                'asset_uri': payload.asset_uri,
                'file_hash': source.file_hash,
                'content_hash': source.package.content_hash,
                'idempotency_key': idempotency_key,
            }
        )
        if payload.submit_review:
            result['review_submission'] = {'status': 'pending'}
        await marketplace_agent_publish_request_service.save_result(
            db,
            request,
            resource_id=result['resource_id'],
            version=result['version'],
            state='committed',
            result=result,
        )
        await self._register_artifact(
            db,
            identity=identity,
            resource_kind=resource_kind,
            result=result,
            title=_package_title(source.package),
            work_session_id=work_session_id,
            idempotency_key=idempotency_key,
            action='create',
        )
        await db.commit()
        if payload.submit_review:
            return await self._complete_review(
                db,
                identity=identity,
                resource_kind=resource_kind,
                request_id=request.id,
                result=result,
                work_session_id=work_session_id,
                idempotency_key=idempotency_key,
            )
        return result

    @staticmethod
    async def _read_and_parse_source(
        db: AsyncSession,
        *,
        owner_hasn_id: str,
        resource_kind: ResourceKind,
        asset_uri: str,
    ) -> PublicationSource:
        match = _ASSET_URI.fullmatch(asset_uri)
        if match is None:
            raise errors.RequestError(msg='asset_uri 必须是 hasn://asset/{asset_id}')
        asset = await HasnAssetService.get_by_asset_id(db, match.group('asset_id'))
        if asset is None:
            raise errors.NotFoundError(msg='发布资产不存在')
        if asset.owner_hasn_id != owner_hasn_id:
            raise errors.ForbiddenError(msg='只能发布当前主人的资产')
        if asset.lifecycle_status != 'active' or asset.object_state != 'active':
            raise errors.ConflictError(msg='发布资产当前不可用')
        content = await StorageService.read_bytes(
            db,
            storage_id=asset.storage_id,
            object_key=asset.object_key,
        )
        if resource_kind == 'skill':
            parsed_package: ParsedPackage = parse_skill_package(content)
        elif resource_kind == 'template':
            parsed_package = parse_template_package(content)
        else:
            parsed_package = parse_skill_pack_package(content)
        return PublicationSource(
            content=content,
            file_hash=hashlib.sha256(content).hexdigest(),
            package=parsed_package,
            filename=asset.original_name,
        )

    @staticmethod
    async def _create_skill(
        db: AsyncSession,
        *,
        identity: AgentTokenPayload,
        payload: AgentMarketplacePublishRequest,
        source: PublicationSource,
    ) -> dict:
        package = source.package
        assert isinstance(package, SkillPackage)
        skill = await marketplace_skill_service.upload_user_skill(
            db=db,
            user_id=identity.owner_user_id,
            hasn_id=identity.owner_hasn_id,
            content=source.content,
            filename=source.filename,
            changelog=payload.changelog,
            requested_visibility=payload.visibility,
            commit=False,
        )
        version = validate_version(str(package.metadata['version']))
        return {
            'resource_id': skill.skill_id,
            'resource_uri': _resource_uri('skill', skill.skill_id),
            'version': version,
            'status': skill.status,
            'visibility': skill.visibility,
            'requested_visibility': skill.requested_visibility,
        }

    @staticmethod
    async def _create_template(
        db: AsyncSession,
        *,
        identity: AgentTokenPayload,
        payload: AgentMarketplacePublishRequest,
        source: PublicationSource,
    ) -> dict:
        package = source.package
        assert isinstance(package, TemplatePackage)
        template = await marketplace_template_service.upload_user_template(
            db=db,
            user_id=identity.owner_user_id,
            hasn_id=identity.owner_hasn_id,
            content=source.content,
            filename=source.filename,
            changelog=payload.changelog,
            requested_visibility=payload.visibility,
            commit=False,
        )
        version = validate_version(str(package.metadata['version']))
        return {
            'resource_id': template.template_id,
            'resource_uri': _resource_uri('template', template.template_id),
            'version': version,
            'status': template.status,
            'visibility': template.visibility,
            'requested_visibility': template.requested_visibility,
        }

    @staticmethod
    async def _create_skill_pack(
        db: AsyncSession,
        *,
        identity: AgentTokenPayload,
        payload: AgentMarketplacePublishRequest,
        source: PublicationSource,
    ) -> dict:
        package = source.package
        assert isinstance(package, SkillPackPackage)
        metadata = package.metadata
        slug = skill_pack_service.normalize_slug(str(metadata.get('slug') or metadata['name']))
        if not slug:
            raise errors.RequestError(msg='bundle.yaml 无法生成有效 slug')
        version = validate_version(str(metadata['version']))
        namespace = f'user/{identity.owner_hasn_id}'
        snapshot = await skill_pack_service.upsert_skill_pack(
            db,
            SkillPackCreateRequest(
                namespace=namespace,
                name=str(metadata['name']),
                description=str(metadata['description']),
                category=metadata.get('category'),
                bundle_slug=slug,
                command_key=str(metadata.get('command_key') or f'/{slug}'),
                version=version,
                hermes_bundle_json=metadata,
                hermes_yaml=package.hermes_yaml,
                is_private=True,
                is_official=False,
                status='draft',
            ),
            author_id=identity.owner_user_id,
        )
        template = await db.scalar(
            select(MarketplaceTemplate).where(
                MarketplaceTemplate.template_id == snapshot['template_id']
            )
        )
        if template is None:
            raise errors.ServerError(msg='技能包草稿创建后无法读取')
        template.hasn_id = identity.owner_hasn_id
        template.author_name = identity.owner_hasn_id
        template.visibility = 'private'
        template.requested_visibility = payload.visibility
        template.is_private = True
        template.source_type = 'user'
        package_url, file_hash, file_size = await marketplace_storage_service.upload_template_package(
            db=db,
            template_id=template.template_id,
            version=version,
            content=source.content,
        )
        version_row = await db.scalar(
            select(MarketplaceTemplateVersion).where(
                MarketplaceTemplateVersion.template_id == template.template_id,
                MarketplaceTemplateVersion.version == version,
            )
        )
        if version_row is None:
            raise errors.ServerError(msg='技能包版本创建后无法读取')
        version_row.changelog = payload.changelog
        version_row.package_url = package_url
        version_row.file_hash = file_hash
        version_row.file_size = file_size
        await db.flush()
        return {
            'resource_id': template.template_id,
            'resource_uri': _resource_uri('skill_pack', template.template_id),
            'version': version,
            'status': template.status,
            'visibility': template.visibility,
            'requested_visibility': template.requested_visibility,
            'member_skill_ids': snapshot['skill_ids'],
        }

    async def _complete_review(
        self,
        db: AsyncSession,
        *,
        identity: AgentTokenPayload,
        resource_kind: ResourceKind,
        request_id: int,
        result: dict,
        work_session_id: str | None,
        idempotency_key: str,
    ) -> dict:
        try:
            if resource_kind == 'skill':
                skill = await marketplace_skill_service.get_by_resource_id_for_user(
                    db=db,
                    resource_id=result['resource_id'],
                    user_id=identity.owner_user_id,
                )
                if skill.status != 'pending_review':
                    skill = await marketplace_skill_service.submit_review(
                        db=db,
                        resource_id=result['resource_id'],
                        user_id=identity.owner_user_id,
                    )
                resource_status = skill.status
                resource_visibility = skill.visibility
                title = skill.name
            else:
                template = await marketplace_template_service.get_by_resource_id_for_user(
                    db=db,
                    resource_id=result['resource_id'],
                    user_id=identity.owner_user_id,
                )
                if template.status != 'pending_review':
                    template = await marketplace_template_service.submit_review(
                        db=db,
                        resource_id=result['resource_id'],
                        user_id=identity.owner_user_id,
                    )
                resource_status = template.status
                resource_visibility = template.visibility
                title = template.name
            completed = {
                **result,
                'status': resource_status,
                'visibility': resource_visibility,
                'review_submission': {'status': 'submitted'},
            }
            await self._register_artifact(
                db,
                identity=identity,
                resource_kind=resource_kind,
                result=completed,
                title=title,
                work_session_id=work_session_id,
                idempotency_key=idempotency_key,
                action='update',
            )
            await self._update_request_result(db, request_id=request_id, state='committed', result=completed)
            return completed
        except Exception as exc:
            await db.rollback()
            partial = {
                **result,
                'review_submission': {'status': 'failed', 'error': str(exc)},
            }
            log.warning('市场草稿已创建，但提审失败: %s', exc)
            await self._update_request_result(db, request_id=request_id, state='partial', result=partial)
            return partial

    @staticmethod
    async def _register_artifact(
        db: AsyncSession,
        *,
        identity: AgentTokenPayload,
        resource_kind: ResourceKind,
        result: dict,
        title: str,
        work_session_id: str | None,
        idempotency_key: str,
        action: Literal['create', 'update'],
    ) -> None:
        await register_app_resource_artifact(
            db,
            app_id='marketplace',
            resource_kind=_ARTIFACT_KINDS[resource_kind],
            server_id=str(result['resource_id']),
            agent_hasn_id=identity.agent_hasn_id,
            owner_hasn_id=identity.owner_hasn_id,
            title=title,
            source_tool=_SOURCE_TOOLS[resource_kind],
            session_id=work_session_id,
            action=action,
            dispatch_id=idempotency_key,
            metadata={
                'version': str(result['version']),
                'status': str(result['status']),
            },
        )

    @staticmethod
    async def _update_request_result(
        db: AsyncSession,
        *,
        request_id: int,
        state: str,
        result: dict,
    ) -> None:
        request = await db.get(MarketplaceAgentPublishRequest, request_id)
        if request is None:
            raise errors.ServerError(msg='发布幂等记录不存在')
        request.state = state
        request.result = result
        await db.commit()


def _resource_uri(resource_kind: ResourceKind, resource_id: str) -> str:
    descriptor = ai_native_app_registry.resource_descriptor('marketplace', _ARTIFACT_KINDS[resource_kind])
    if descriptor is None:
        raise errors.ServerError(msg=f'能力市场缺少 {_ARTIFACT_KINDS[resource_kind]} 资源描述符')
    return descriptor.build_uri(resource_id)


def _package_title(package: ParsedPackage) -> str:
    title = package.metadata.get('display_name') or package.metadata.get('name') or package.metadata.get('slug')
    if not title:
        raise errors.ServerError(msg='能力市场发布包缺少资源标题')
    return str(title)


agent_publish_service = AgentPublishService()

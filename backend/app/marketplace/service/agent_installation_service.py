"""Agent JWT 技能与技能包装卸的云端权威服务。"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnAgents
from backend.app.hasn.service.hasn_agents_service import agent_profile_service
from backend.app.marketplace.model import (
    MarketplacePersonalSkill,
    MarketplaceSkill,
    MarketplaceTemplate,
    MarketplaceTemplateVersion,
)
from backend.app.marketplace.service import skill_pack_service
from backend.app.marketplace.service.agent_profile_sources import (
    build_agent_profile_skill_sources,
    classify_stored_skill_refs,
    get_personal_skill_immutable_snapshots,
)
from backend.app.marketplace.service.common_skills_service import (
    get_common_skill_snapshot,
    get_skills_immutable_snapshots,
)
from backend.app.marketplace.service.resource_id import parse_resource_id
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors


class AgentMarketplaceInstallationService:
    """只作用于凭证中的当前 Agent，不接受调用方身份参数。"""

    @staticmethod
    async def _get_agent(db: AsyncSession, identity: AgentTokenPayload) -> HasnAgents:
        row = (
            await db.execute(
                sa.select(HasnAgents)
                .where(
                    HasnAgents.hasn_id == identity.agent_hasn_id,
                    HasnAgents.owner_id == identity.owner_hasn_id,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='ERR_HASN_AGENT_NOT_FOUND')
        return row

    @staticmethod
    async def _append_sync_event(db: AsyncSession, row: HasnAgents) -> None:
        await agent_profile_service.gateway.append_agent_sync_event(
            db,
            owner_id=row.owner_id,
            agent=row,
            event_type='agent.updated',
        )

    @staticmethod
    async def _is_personal_reference(
        db: AsyncSession,
        *,
        identity: AgentTokenPayload,
        resource_id: str,
    ) -> bool:
        return (
            await db.execute(
                sa.select(MarketplacePersonalSkill.id)
                .where(
                    sa.or_(
                        MarketplacePersonalSkill.user_id == identity.owner_user_id,
                        MarketplacePersonalSkill.hasn_id == identity.owner_hasn_id,
                    ),
                    sa.or_(
                        MarketplacePersonalSkill.personal_skill_id == resource_id,
                        MarketplacePersonalSkill.slug == resource_id,
                    ),
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None

    async def _state(
        self,
        db: AsyncSession,
        *,
        identity: AgentTokenPayload,
        row: HasnAgents,
        changed: bool,
    ) -> dict[str, Any]:
        common_ids, _ = await get_common_skill_snapshot(db)
        sources = await build_agent_profile_skill_sources(
            db,
            stored_skill_refs=row.skills,
            stored_bundle_refs=row.skill_bundles,
            common_skill_ids=common_ids,
            owner_user_id=identity.owner_user_id,
            owner_hasn_id=identity.owner_hasn_id,
        )
        skill_versions = await get_skills_immutable_snapshots(db, sources.effective_skill_ids)
        skill_versions.update(
            await get_personal_skill_immutable_snapshots(
                db,
                personal_skill_ids=sources.personal_skill_ids,
                owner_user_id=identity.owner_user_id,
                owner_hasn_id=identity.owner_hasn_id,
            )
        )
        return {
            'changed': changed,
            'profile_revision': int(row.profile_revision or 1),
            'direct_skill_ids': sources.direct_skill_ids,
            'personal_skill_ids': sources.personal_skill_ids,
            'effective_skill_ids': sources.effective_skill_ids,
            'origins': sources.origins,
            'skill_versions': skill_versions,
            'skill_bundles': sources.skill_bundles,
        }

    async def get_installed(
        self,
        db: AsyncSession,
        *,
        identity: AgentTokenPayload,
    ) -> dict[str, Any]:
        """读取当前 Agent 的云端权威期望态；节点物化态由 daemon 在本地合并。"""
        row = await self._get_agent(db, identity)
        state = await self._state(db, identity=identity, row=row, changed=False)
        state.pop('changed', None)
        return state

    async def install_skill(
        self,
        db: AsyncSession,
        *,
        identity: AgentTokenPayload,
        resource_id: str,
    ) -> dict[str, Any]:
        if await self._is_personal_reference(db, identity=identity, resource_id=resource_id):
            raise errors.NotFoundError(msg='ERR_MARKETPLACE_SKILL_NOT_FOUND')
        try:
            namespace, slug = parse_resource_id(resource_id)
        except errors.RequestError as exc:
            raise errors.NotFoundError(msg='ERR_MARKETPLACE_SKILL_NOT_FOUND') from exc
        canonical_id = f'{namespace}/{slug}'
        skill = (
            await db.execute(
                sa.select(MarketplaceSkill)
                .where(
                    MarketplaceSkill.skill_id == canonical_id,
                    MarketplaceSkill.status == 'published',
                    sa.or_(
                        MarketplaceSkill.visibility == 'public',
                        MarketplaceSkill.user_id == identity.owner_user_id,
                        MarketplaceSkill.hasn_id == identity.owner_hasn_id,
                    ),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if skill is None:
            raise errors.NotFoundError(msg='ERR_MARKETPLACE_SKILL_NOT_FOUND')

        row = await self._get_agent(db, identity)
        direct_ids, personal_ids = await classify_stored_skill_refs(
            db,
            stored_refs=row.skills,
            owner_user_id=identity.owner_user_id,
            owner_hasn_id=identity.owner_hasn_id,
        )
        changed = canonical_id not in direct_ids
        if changed:
            row.skills = [*direct_ids, *personal_ids, canonical_id]
            row.profile_revision = int(row.profile_revision or 1) + 1
            await db.flush()
            await self._append_sync_event(db, row)
        return await self._state(db, identity=identity, row=row, changed=changed)

    async def uninstall_skill(
        self,
        db: AsyncSession,
        *,
        identity: AgentTokenPayload,
        resource_id: str,
    ) -> dict[str, Any]:
        row = await self._get_agent(db, identity)
        direct_ids, personal_ids = await classify_stored_skill_refs(
            db,
            stored_refs=row.skills,
            owner_user_id=identity.owner_user_id,
            owner_hasn_id=identity.owner_hasn_id,
        )
        if resource_id in personal_ids or await self._is_personal_reference(
            db,
            identity=identity,
            resource_id=resource_id,
        ):
            raise errors.ConflictError(
                msg='personal_skill_owner_managed: 个人技能只能通过 Owner Interface 解绑'
            )
        try:
            namespace, slug = parse_resource_id(resource_id)
        except errors.RequestError as exc:
            raise errors.NotFoundError(msg='ERR_MARKETPLACE_SKILL_NOT_FOUND') from exc
        canonical_id = f'{namespace}/{slug}'
        common_ids, _ = await get_common_skill_snapshot(db)
        if canonical_id in common_ids:
            raise errors.ConflictError(msg='common_skill_cannot_uninstall: 公共技能不能由 Agent 卸载')

        changed = canonical_id in direct_ids
        if changed:
            row.skills = [skill_id for skill_id in direct_ids if skill_id != canonical_id] + personal_ids
            row.profile_revision = int(row.profile_revision or 1) + 1
            await db.flush()
            await self._append_sync_event(db, row)
        return await self._state(db, identity=identity, row=row, changed=changed)

    @staticmethod
    async def _resolve_pack_version(
        db: AsyncSession,
        *,
        identity: AgentTokenPayload,
        package_id: str,
        version: str | None,
    ) -> tuple[MarketplaceTemplateVersion, dict[str, Any]]:
        stmt = (
            sa.select(MarketplaceTemplateVersion)
            .join(
                MarketplaceTemplate,
                MarketplaceTemplate.template_id == MarketplaceTemplateVersion.template_id,
            )
            .where(
                MarketplaceTemplate.template_id == package_id,
                MarketplaceTemplate.template_type == 'skill_pack',
                MarketplaceTemplate.status == 'published',
                sa.or_(
                    MarketplaceTemplate.visibility == 'public',
                    MarketplaceTemplate.user_id == identity.owner_user_id,
                    MarketplaceTemplate.hasn_id == identity.owner_hasn_id,
                ),
            )
        )
        if version:
            stmt = stmt.where(MarketplaceTemplateVersion.version == version)
        else:
            stmt = stmt.where(MarketplaceTemplateVersion.is_latest.is_(True))
        row = (await db.execute(stmt.order_by(MarketplaceTemplateVersion.id.desc()).limit(1))).scalar_one_or_none()
        if row is None:
            raise errors.NotFoundError(msg='ERR_MARKETPLACE_SKILL_PACK_NOT_FOUND')

        # Runtime 校验的是权威 Hermes definition 本身。历史版本行的 content_hash 可能仍是
        # 发布制品 manifest 指纹，不能把它冻结进 Agent Profile，否则同一份 hermes_yaml
        # 会在 Runtime 侧被判为哈希不一致。
        hermes_yaml = str(row.hermes_yaml or '')
        frozen_hash = skill_pack_service.content_hash(hermes_yaml) if hermes_yaml else ''
        bundle_slug = str(row.bundle_slug or '').strip()
        if not frozen_hash or not bundle_slug or not hermes_yaml:
            raise errors.RequestError(
                code=422,
                msg='skill_pack_snapshot_incomplete: 技能包版本缺少冻结指纹、slug 或定义',
            )
        frozen_ref = {
            'package_id': package_id,
            'version': row.version,
            'content_hash': frozen_hash,
            'bundle_slug': bundle_slug,
        }
        return row, frozen_ref

    async def install_skill_pack(
        self,
        db: AsyncSession,
        *,
        identity: AgentTokenPayload,
        package_id: str,
        version: str | None,
    ) -> dict[str, Any]:
        version_row, frozen_ref = await self._resolve_pack_version(
            db,
            identity=identity,
            package_id=package_id,
            version=version,
        )
        row = await self._get_agent(db, identity)
        current = [ref for ref in (row.skill_bundles or []) if isinstance(ref, dict)]
        position = next(
            (
                index
                for index, ref in enumerate(current)
                if (ref.get('package_id') or ref.get('template_id')) == package_id
                and ref.get('version') == version_row.version
            ),
            None,
        )
        changed = position is None or current[position] != frozen_ref
        if changed:
            if position is None:
                current.append(frozen_ref)
            else:
                current[position] = frozen_ref
            row.skill_bundles = current
            row.profile_revision = int(row.profile_revision or 1) + 1
            await db.flush()
            await self._append_sync_event(db, row)

        state = await self._state(db, identity=identity, row=row, changed=changed)
        member_skill_ids = skill_pack_service.member_skill_ids(str(version_row.hermes_yaml))
        state['bundle'] = {
            **frozen_ref,
            'command_key': version_row.command_key,
            'hermes_yaml': version_row.hermes_yaml,
            'member_skill_ids': member_skill_ids,
            'member_skills': await skill_pack_service.resolve_member_skill_snapshots(
                db,
                member_skill_ids,
                version_row.skill_dependencies_versioned,
            ),
        }
        return state

    async def uninstall_skill_pack(
        self,
        db: AsyncSession,
        *,
        identity: AgentTokenPayload,
        package_id: str,
        version: str | None,
    ) -> dict[str, Any]:
        row = await self._get_agent(db, identity)
        current = [ref for ref in (row.skill_bundles or []) if isinstance(ref, dict)]
        matching = [
            ref
            for ref in current
            if (ref.get('package_id') or ref.get('template_id')) == package_id
            and (version is None or ref.get('version') == version)
        ]
        if version is None and len(matching) > 1:
            candidates = sorted({str(ref.get('version') or '') for ref in matching})
            raise errors.ConflictError(
                msg='skill_pack_version_required: 已安装多个固定版本，请指定 version',
                data={'candidates': candidates},
            )

        changed = bool(matching)
        if changed:
            selected = matching[0]
            row.skill_bundles = [ref for ref in current if ref is not selected]
            row.profile_revision = int(row.profile_revision or 1) + 1
            await db.flush()
            await self._append_sync_event(db, row)
        return await self._state(db, identity=identity, row=row, changed=changed)


agent_installation_service = AgentMarketplaceInstallationService()

from __future__ import annotations

# knowledge 应用 ID（凭据下发/provisioning 已退役，仅企业实例登记面仍引用）
KNOWLEDGE_APP_ID = 'knowledge'

import re
import secrets

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from pypinyin import Style, lazy_pinyin

from backend.app.hasn.model import (
    HasnAppInstance,
    HasnEnterprise,
    HasnEnterpriseInviteCode,
    HasnEnterpriseMembership,
    HasnUserActiveWorkspace,
    HasnWorkspaceApp,
)
from backend.app.admin.model.user import User
from backend.app.hasn.service import workspace_notification_subscriber as _workspace_notifications  # noqa: F401
from backend.app.hasn.service.enterprise_application_service import InviteCodePolicy
from backend.app.hasn.service.enterprise_event_bus import EnterpriseEventBus, enterprise_event_bus
from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
from backend.app.hasn.service.instance_resolver import (
    FACE_UI,
    InstanceResolutionError,
    instance_resolver,
)
# RF-CLOUD：数据面中转已删，RAGFlowClient 不再被本服务引用，故合并时去掉该 import。
from backend.app.hasn.service.workbench_app_registry import workbench_app_registry
from backend.app.hasn.service.workbench_event_bus import workbench_event_bus
from backend.app.hasn.service import app_catalog_service
from backend.common.security.encryption import key_encryption
from backend.common.exception import errors
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession


class WorkbenchDomainService:
    def __init__(
        self,
        *,
        enterprise_bus: EnterpriseEventBus = enterprise_event_bus,
        workbench_bus: EnterpriseEventBus = workbench_event_bus,
    ) -> None:
        # RF-CLOUD：云端不再直连 RagFlow 数据面，故移除 ragflow_client_factory。
        self.enterprise_bus = enterprise_bus
        self.workbench_bus = workbench_bus

    async def create_enterprise(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        name: str,
        description: str | None = None,
        logo: str | None = None,
        industry: str | None = None,
        company_size: str | None = None,
        join_policy: str = 'invite_only',
        slug: str | None = None,
    ) -> dict[str, Any]:
        slug = slug or await _generate_unique_enterprise_slug(db, name)
        existing = await _scalar(db, sa.select(HasnEnterprise).where(HasnEnterprise.slug == slug))
        if existing:
            raise errors.ConflictError(msg='企业标识已存在')

        enterprise = HasnEnterprise(
            name=name,
            slug=slug,
            logo=logo,
            industry=industry,
            company_size=company_size,
            description=description,
            owner_user_id=user_id,
            join_policy=join_policy,
            status='active',
        )
        db.add(enterprise)
        await db.flush()
        await db.refresh(enterprise)

        db.add(
            HasnEnterpriseMembership(
                enterprise_id=enterprise.id,
                user_id=user_id,
                role='owner',
                status='approved',
                apply_via='owner_create',
                decided_by=user_id,
                decided_at=timezone.now(),
            )
        )
        await self.ensure_auto_apps(
            db,
            workspace_kind='enterprise',
            user_id=None,
            enterprise_id=enterprise.id,
            enabled_by=user_id,
        )
        await db.flush()
        await self.enterprise_bus.publish(
            'on_enterprise_created',
            {'enterprise_id': enterprise.id, 'owner_user_id': user_id},
        )
        return _enterprise_payload(enterprise)

    async def get_enterprise(self, db: AsyncSession, enterprise_id: int) -> dict[str, Any]:
        enterprise = await self._get_enterprise_model(db, enterprise_id)
        return _enterprise_payload(enterprise)

    async def update_enterprise(
        self,
        db: AsyncSession,
        *,
        enterprise_id: int,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        enterprise = await self._get_enterprise_model(db, enterprise_id)
        for field in ('name', 'logo', 'industry', 'company_size', 'description', 'join_policy', 'status'):
            if field in updates:
                setattr(enterprise, field, updates[field])
        if hasattr(enterprise, 'updated_at'):
            enterprise.updated_at = timezone.now()
        await db.flush()
        await db.refresh(enterprise)
        return _enterprise_payload(enterprise)

    async def delete_enterprise(self, db: AsyncSession, *, enterprise_id: int) -> None:
        enterprise = await self._get_enterprise_model(db, enterprise_id)
        enterprise.status = 'deleted'
        if hasattr(enterprise, 'updated_at'):
            enterprise.updated_at = timezone.now()

        members = (
            (
                await db.execute(
                    sa.select(HasnEnterpriseMembership).where(
                        HasnEnterpriseMembership.enterprise_id == enterprise_id,
                        HasnEnterpriseMembership.status == 'approved',
                    )
                )
            )
            .scalars()
            .all()
        )
        member_user_ids = [int(member.user_id) for member in members]
        for member in members:
            member.status = 'removed'
            await self._fallback_to_personal_if_active(db, user_id=member.user_id, enterprise_id=enterprise_id)
        await db.flush()
        await self.enterprise_bus.publish(
            'on_enterprise_disbanded',
            {'enterprise_id': enterprise_id, 'member_user_ids': member_user_ids},
        )

    async def search_enterprises(self, db: AsyncSession, *, q: str = '') -> dict[str, Any]:
        stmt = sa.select(HasnEnterprise).where(HasnEnterprise.status == 'active')
        if q:
            stmt = stmt.where(HasnEnterprise.name.ilike(f'%{q}%') | HasnEnterprise.slug.ilike(f'%{q}%'))
        items = (await db.execute(stmt.order_by(HasnEnterprise.id.desc()))).scalars().all()
        return {'items': [_enterprise_payload(item) for item in items], 'q': q}

    async def list_members(self, db: AsyncSession, *, enterprise_id: int) -> dict[str, Any]:
        members = (
            (
                await db.execute(
                    sa
                    .select(HasnEnterpriseMembership, User)
                    .outerjoin(User, User.id == HasnEnterpriseMembership.user_id)
                    .where(HasnEnterpriseMembership.enterprise_id == enterprise_id)
                    .order_by(HasnEnterpriseMembership.id.asc())
                )
            )
            .all()
        )
        return {
            'items': [_membership_payload(member, user) for member, user in members],
            'enterprise_id': enterprise_id,
        }

    async def apply_enterprise(
        self,
        db: AsyncSession,
        *,
        enterprise_id: int,
        user_id: int,
        apply_message: str | None,
        invite_code: str | None,
    ) -> dict[str, Any]:
        enterprise = await self._get_enterprise_model(db, enterprise_id)
        if enterprise.status != 'active':
            raise errors.RequestError(msg='enterprise_not_active')
        if enterprise.join_policy == 'closed':
            raise errors.RequestError(msg='enterprise_closed')

        existing = await _scalar(
            db,
            sa.select(HasnEnterpriseMembership).where(
                HasnEnterpriseMembership.enterprise_id == enterprise_id,
                HasnEnterpriseMembership.user_id == user_id,
                HasnEnterpriseMembership.status.in_(('pending', 'approved')),
            ),
        )
        if existing:
            return _membership_payload(existing)

        status = 'pending'
        apply_via = 'manual'
        decided_at: datetime | None = None
        code_record = None
        if invite_code:
            code_record = await _scalar(
                db,
                sa.select(HasnEnterpriseInviteCode).where(
                    HasnEnterpriseInviteCode.enterprise_id == enterprise_id,
                    HasnEnterpriseInviteCode.code == invite_code,
                ),
            )
            if code_record is None:
                raise errors.RequestError(msg='invite_code_not_found')
            invalid_reason = InviteCodePolicy(
                max_uses=code_record.max_uses,
                used_count=code_record.used_count,
                revoked=code_record.revoked,
                expires_at=code_record.expires_at,
            ).validate()
            if invalid_reason:
                raise errors.RequestError(msg=invalid_reason)
            code_record.used_count += 1
            apply_via = 'invite_code'
            if code_record.auto_approve:
                status = 'approved'
                decided_at = timezone.now()

        membership = HasnEnterpriseMembership(
            enterprise_id=enterprise_id,
            user_id=user_id,
            role='member',
            status=status,
            apply_message=apply_message,
            apply_via=apply_via,
            invite_code=invite_code,
            decided_at=decided_at,
        )
        db.add(membership)
        await db.flush()
        await db.refresh(membership)
        if membership.status == 'approved':
            await self.enterprise_bus.publish(
                'on_member_approved',
                {'enterprise_id': enterprise_id, 'user_id': user_id},
            )
        return _membership_payload(membership)

    async def list_applications(
        self, db: AsyncSession, *, enterprise_id: int, status: str = 'pending'
    ) -> dict[str, Any]:
        rows = (
            (
                await db.execute(
                    sa.select(HasnEnterpriseMembership).where(
                        HasnEnterpriseMembership.enterprise_id == enterprise_id,
                        HasnEnterpriseMembership.status == status,
                    )
                )
            )
            .scalars()
            .all()
        )
        return {'items': [_membership_payload(row) for row in rows], 'enterprise_id': enterprise_id, 'status': status}

    async def approve_application(
        self,
        db: AsyncSession,
        *,
        enterprise_id: int,
        app_id: int,
        decided_by: int,
    ) -> dict[str, Any]:
        membership = await self._get_membership_model(db, enterprise_id=enterprise_id, membership_id=app_id)
        membership.status = 'approved'
        membership.decided_by = decided_by
        membership.decided_at = timezone.now()
        if hasattr(membership, 'updated_at'):
            membership.updated_at = timezone.now()
        await db.flush()
        await self.enterprise_bus.publish(
            'on_member_approved',
            {'enterprise_id': enterprise_id, 'user_id': membership.user_id},
        )
        return _membership_payload(membership)

    async def reject_application(
        self,
        db: AsyncSession,
        *,
        enterprise_id: int,
        app_id: int,
        decided_by: int,
        note: str | None,
    ) -> dict[str, Any]:
        membership = await self._get_membership_model(db, enterprise_id=enterprise_id, membership_id=app_id)
        membership.status = 'rejected'
        membership.decided_by = decided_by
        membership.decided_at = timezone.now()
        membership.decision_note = note
        if hasattr(membership, 'updated_at'):
            membership.updated_at = timezone.now()
        await db.flush()
        return _membership_payload(membership)

    async def remove_member(self, db: AsyncSession, *, enterprise_id: int, user_id: int) -> None:
        membership = await _scalar(
            db,
            sa.select(HasnEnterpriseMembership).where(
                HasnEnterpriseMembership.enterprise_id == enterprise_id,
                HasnEnterpriseMembership.user_id == user_id,
                HasnEnterpriseMembership.status == 'approved',
            ),
        )
        if membership is None:
            raise errors.NotFoundError(msg='企业成员不存在')
        membership.status = 'left'
        if hasattr(membership, 'updated_at'):
            membership.updated_at = timezone.now()
        await self._fallback_to_personal_if_active(db, user_id=user_id, enterprise_id=enterprise_id)
        await db.flush()
        await self.enterprise_bus.publish('on_member_left', {'enterprise_id': enterprise_id, 'user_id': user_id})

    async def create_invite_code(
        self,
        db: AsyncSession,
        *,
        enterprise_id: int,
        created_by: int,
        code: str | None = None,
        max_uses: int | None = None,
        expires_at: datetime | None = None,
        auto_approve: bool = False,
    ) -> dict[str, Any]:
        await self._get_enterprise_model(db, enterprise_id)
        code = code or secrets.token_urlsafe(12)[:16]
        invite = HasnEnterpriseInviteCode(
            enterprise_id=enterprise_id,
            code=code,
            created_by=created_by,
            max_uses=max_uses,
            used_count=0,
            expires_at=expires_at,
            auto_approve=auto_approve,
            revoked=False,
        )
        db.add(invite)
        await db.flush()
        await db.refresh(invite)
        return _invite_payload(invite)

    async def list_invite_codes(self, db: AsyncSession, *, enterprise_id: int) -> dict[str, Any]:
        rows = (
            (
                await db.execute(
                    sa
                    .select(HasnEnterpriseInviteCode)
                    .where(HasnEnterpriseInviteCode.enterprise_id == enterprise_id)
                    .order_by(HasnEnterpriseInviteCode.id.desc())
                )
            )
            .scalars()
            .all()
        )
        return {'items': [_invite_payload(row) for row in rows], 'enterprise_id': enterprise_id}

    async def revoke_invite_code(self, db: AsyncSession, *, enterprise_id: int, code: str) -> dict[str, Any]:
        invite = await _scalar(
            db,
            sa.select(HasnEnterpriseInviteCode).where(
                HasnEnterpriseInviteCode.enterprise_id == enterprise_id,
                HasnEnterpriseInviteCode.code == code,
            ),
        )
        if invite is None:
            raise errors.NotFoundError(msg='邀请码不存在')
        invite.revoked = True
        await db.flush()
        return _invite_payload(invite)

    async def list_user_workspaces(self, db: AsyncSession, *, user_id: int) -> dict[str, Any]:
        active = await self.get_active_workspace(db, user_id=user_id)
        rows = (
            await db.execute(
                sa
                .select(HasnEnterpriseMembership, HasnEnterprise)
                .join(HasnEnterprise, HasnEnterprise.id == HasnEnterpriseMembership.enterprise_id)
                .where(
                    HasnEnterpriseMembership.user_id == user_id,
                    HasnEnterpriseMembership.status == 'approved',
                    HasnEnterprise.status == 'active',
                )
                .order_by(HasnEnterprise.name.asc())
            )
        ).all()
        enterprise_ids = [int(enterprise.id) for _, enterprise in rows]
        enterprise_stats = await self._enterprise_workspace_stats(db, enterprise_ids=enterprise_ids)
        personal_stats = await self._personal_workspace_stats(db, user_id=user_id)
        available = [
            {
                'kind': 'personal',
                'name': '个人空间',
                'role': 'owner',
                'enterprise_id': None,
                'description': None,
                'created_at': None,
                'joined_at': None,
                **personal_stats,
            }
        ]
        available.extend(
            {
                'kind': 'enterprise',
                'enterprise_id': enterprise.id,
                'name': enterprise.name,
                'slug': enterprise.slug,
                'role': membership.role,
                'logo': getattr(enterprise, 'logo', None),
                'industry': getattr(enterprise, 'industry', None),
                'company_size': getattr(enterprise, 'company_size', None),
                'description': getattr(enterprise, 'description', None),
                'created_at': _datetime_payload(getattr(enterprise, 'created_at', None)),
                'joined_at': _datetime_payload(getattr(membership, 'created_at', None)),
                **enterprise_stats.get(int(enterprise.id), _empty_workspace_stats()),
            }
            for membership, enterprise in rows
        )
        return {'active': active, 'available': available, 'user_id': user_id}

    async def _personal_workspace_stats(self, db: AsyncSession, *, user_id: int) -> dict[str, int]:
        app_count = await _scalar(
            db,
            sa
            .select(sa.func.count())
            .select_from(HasnWorkspaceApp)
            .where(
                HasnWorkspaceApp.workspace_kind == 'personal',
                HasnWorkspaceApp.user_id == user_id,
                HasnWorkspaceApp.status == 'active',
            ),
        )
        return {
            'member_count': 1,
            'app_count': int(app_count or 0),
            'admin_count': 1,
        }

    async def _enterprise_workspace_stats(
        self,
        db: AsyncSession,
        *,
        enterprise_ids: list[int],
    ) -> dict[int, dict[str, int]]:
        if not enterprise_ids:
            return {}

        member_counts = await _grouped_count(
            db,
            sa
            .select(HasnEnterpriseMembership.enterprise_id, sa.func.count())
            .where(
                HasnEnterpriseMembership.enterprise_id.in_(enterprise_ids),
                HasnEnterpriseMembership.status == 'approved',
            )
            .group_by(HasnEnterpriseMembership.enterprise_id),
        )
        admin_counts = await _grouped_count(
            db,
            sa
            .select(HasnEnterpriseMembership.enterprise_id, sa.func.count())
            .where(
                HasnEnterpriseMembership.enterprise_id.in_(enterprise_ids),
                HasnEnterpriseMembership.status == 'approved',
                HasnEnterpriseMembership.role.in_(('owner', 'admin')),
            )
            .group_by(HasnEnterpriseMembership.enterprise_id),
        )
        app_counts = await _grouped_count(
            db,
            sa
            .select(HasnWorkspaceApp.enterprise_id, sa.func.count())
            .where(
                HasnWorkspaceApp.workspace_kind == 'enterprise',
                HasnWorkspaceApp.enterprise_id.in_(enterprise_ids),
                HasnWorkspaceApp.status == 'active',
            )
            .group_by(HasnWorkspaceApp.enterprise_id),
        )

        return {
            enterprise_id: {
                'member_count': member_counts.get(enterprise_id, 0),
                'app_count': app_counts.get(enterprise_id, 0),
                'admin_count': admin_counts.get(enterprise_id, 0),
            }
            for enterprise_id in enterprise_ids
        }

    async def get_active_workspace(self, db: AsyncSession, *, user_id: int) -> dict[str, Any]:
        active = await _scalar(
            db,
            sa.select(HasnUserActiveWorkspace).where(HasnUserActiveWorkspace.user_id == user_id),
        )
        if active is None:
            return {'kind': 'personal', 'enterprise_id': None}
        if active.kind == 'enterprise':
            membership = await self._approved_membership(db, enterprise_id=active.enterprise_id, user_id=user_id)
            if membership is None:
                await self._set_active_workspace(db, user_id=user_id, kind='personal', enterprise_id=None)
                return {'kind': 'personal', 'enterprise_id': None}
        return {'kind': active.kind, 'enterprise_id': active.enterprise_id}

    async def switch_active_workspace(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        kind: str,
        enterprise_id: int | None,
    ) -> dict[str, Any]:
        if kind not in {'personal', 'enterprise'}:
            raise errors.RequestError(msg='invalid_workspace_kind')
        if kind == 'personal' and enterprise_id is not None:
            raise errors.RequestError(msg='personal workspace cannot have enterprise_id')
        if kind == 'enterprise':
            if enterprise_id is None:
                raise errors.RequestError(msg='enterprise workspace requires enterprise_id')
            membership = await self._approved_membership(db, enterprise_id=enterprise_id, user_id=user_id)
            if membership is None:
                raise errors.ForbiddenError(msg='未加入该企业')

        prev = await self.get_active_workspace(db, user_id=user_id)
        next_workspace = await self._set_active_workspace(
            db,
            user_id=user_id,
            kind=kind,
            enterprise_id=enterprise_id,
        )
        await db.flush()
        await self.enterprise_bus.publish(
            'on_workspace_switched',
            {'user_id': user_id, 'prev_workspace': prev, 'next_workspace': next_workspace},
        )
        return next_workspace

    async def ensure_auto_apps(
        self,
        db: AsyncSession,
        *,
        workspace_kind: str,
        user_id: int | None,
        enterprise_id: int | None,
        enabled_by: int | None,
    ) -> list[dict[str, Any]]:
        rows = []
        for app in workbench_app_registry.auto_install_apps(workspace_kind):
            row = await self._upsert_workspace_app(
                db,
                workspace_kind=workspace_kind,
                user_id=user_id,
                enterprise_id=enterprise_id,
                app_id=app.id,
                status='active',
                enabled_by=enabled_by,
            )
            rows.append(_workspace_app_payload(row))
        return rows

    async def list_current_workspace_apps(self, db: AsyncSession, *, user_id: int) -> list[dict[str, Any]]:
        workspace = await self.get_active_workspace(db, user_id=user_id)
        await self.ensure_auto_apps(
            db,
            workspace_kind=workspace['kind'],
            user_id=user_id if workspace['kind'] == 'personal' else None,
            enterprise_id=workspace['enterprise_id'],
            enabled_by=user_id,
        )
        rows = await self._workspace_app_rows(db, workspace=workspace, user_id=user_id)
        manifests = []
        for row in rows:
            if row.status != 'active':
                continue
            manifest = workbench_app_registry.get(row.app_id).to_manifest(workspace_kind=workspace['kind'])
            manifest['status'] = row.status
            manifest['workspace_kind'] = row.workspace_kind
            manifests.append(manifest)
        return manifests

    async def list_workbench_apps(
        self, db: AsyncSession, *, user_id: int, workspace_kind: str | None = None
    ) -> list[dict[str, Any]]:
        workspace = await self.get_active_workspace(db, user_id=user_id)
        effective_kind = workspace_kind or workspace['kind']
        # 防御性幂等播种：生产由启动期 reconcile 保证已 seed（此处仅一次存在性 SELECT、零写）；
        # 兜底未 seed 环境（测试 / seed 失败）也能返回内置应用，不破坏工作台。
        await app_catalog_service.ensure_catalog_seeded(db)
        rows = await self._workspace_app_rows(db, workspace=workspace, user_id=user_id)
        row_by_app_id = {row.app_id: row for row in rows}
        # C2：catalog（DB 权威）取代硬编码 registry 作为展示目录来源（设计 §6.3）。
        # launch 字段（ui_kind/window_url/window_origin）迁移期仍从本地 registry overlay，
        # registry 在 C6 退役后由 daemon 本地提供（设计 §3 边界）。
        reg_by_id = {a.id: a for a in workbench_app_registry.list()}
        # C4 闸门①：每行附 access（§5.2）。owner 维度准入用 owner hasn_id（tier/purchase 实时判定）。
        owner_hasn_id = await app_catalog_service.resolve_owner_hasn_id(db, user_id=user_id)
        apps = []
        for cat in await app_catalog_service.list_published_catalog(db, kind=effective_kind):
            manifest = app_catalog_service.catalog_to_manifest(cat, registry_app=reg_by_id.get(cat.app_id))
            row = row_by_app_id.get(cat.app_id)
            manifest['status'] = row.status if row else 'available'
            manifest['access'] = await app_catalog_service.resolve_app_access(
                db, catalog=cat, owner_hasn_id=owner_hasn_id or ''
            )
            apps.append(manifest)
        return apps

    async def resolve_app_entry(self, db: AsyncSession, *, user_id: int, app_id: str) -> dict[str, Any]:
        """解析应用入口句柄（设计 11 §3.1，doc 11「注册即用」）。

        点开应用时按**当前工作空间**解析实例：
        - 内置 UI 应用（knowledge/community 等，无 AI-Native manifest）→ 云端就地
          (`gateway_internal`)，仅返回 `entry_route` 供客户端原生导航，无凭据。
        - 第三方 AI-Native 应用（有已发布 manifest）→ `InstanceResolver.resolve(face=ui)`
          按企业/公共实例选址；实例未配置等如实抛 15050。

        **凭据绝不下发浏览器**：响应不含 `credential`；daemon_direct 由 daemon 另行持有，
        cloud_relay 的 app secret 只留云端（设计 11 §0.3/§7.2）。
        """
        # C2：应用存在性 + entry_route 以 catalog（DB 权威，仅 published）为准（设计 §6.3）。
        await app_catalog_service.ensure_catalog_seeded(db)  # 防御性幂等（同 list_workbench_apps）
        cat = await app_catalog_service.get_published_catalog(db, app_id=app_id)
        if cat is None:
            raise errors.NotFoundError(msg='工作台应用不存在')

        workspace = await self.get_active_workspace(db, user_id=user_id)
        ws = {'kind': workspace['kind'], 'enterprise_id': workspace['enterprise_id']}

        transport = 'gateway_internal'
        instance_id: str | None = None
        endpoint: str | None = None
        scope = 'public'
        expires_at: str | None = None

        manifest = await ai_native_app_registry.get_published_manifest(db, app_id=app_id)
        if manifest is not None:
            # 应用同时声明了 AI-Native manifest：尝试按工作空间解析外部 UI 实例并补全句柄。
            # 但工作台注册的应用其 `entry_route` 是**客户端原生路由**（如 /community、
            # /workbench/apps/knowledge），UI 导航恒走该原生路由 → gateway_internal；外部实例
            # 只服务于该应用的数据/工具面（由 daemon/MCP 另行解析），实例未配置不应阻断 UI 入口。
            # 因此实例未配置（15050）时如实回落到内置句柄（原生路由仍可用），不伪造外部实例。
            try:
                handle = await instance_resolver.resolve(
                    db, app_id=app_id, workspace=ws, face=FACE_UI, manifest=manifest
                )
            except InstanceResolutionError:
                handle = None
            if handle is not None and not handle.is_internal:
                transport = handle.transport
                instance_id = handle.instance_id
                endpoint = handle.endpoint
                scope = handle.scope
                expires_at = handle.expires_at

        return {
            'app_id': app_id,
            'entry_route': cat.entry_route,
            'transport': transport,
            'instance_id': instance_id,
            'scope': scope,
            'endpoint': endpoint,
            # daemon_direct / frontend_direct 需 daemon 另取短期凭据；UI 面恒不下发凭据。
            'requires_credential': transport in ('daemon_direct', 'frontend_direct'),
            'expires_at': expires_at,
            'workspace': ws,
        }

    async def enable_current_workspace_app(self, db: AsyncSession, *, user_id: int, app_id: str) -> dict[str, Any]:
        # C4 闸门②：挂载前置准入（catalog 为存在性 + 商业化权威）。下架/未准入直接拒，
        # data 带 access（reason/requires/min_tier/price），前端据此弹升级/购买（设计 §5.3②）。
        cat = await app_catalog_service.get_published_catalog(db, app_id=app_id)
        if cat is None:
            raise errors.NotFoundError(msg='工作台应用不存在')
        # 免费 app 直接放行（不必解析 owner / 订阅）；仅付费 app 才判定准入。
        if (cat.access_type or 'free') != 'free':
            owner_hasn_id = await app_catalog_service.resolve_owner_hasn_id(db, user_id=user_id)
            access = await app_catalog_service.resolve_app_access(
                db, catalog=cat, owner_hasn_id=owner_hasn_id or ''
            )
            if not access['allowed']:
                raise errors.ForbiddenError(msg=access['reason'], data=access)
        workspace = await self.get_active_workspace(db, user_id=user_id)
        row = await self._upsert_workspace_app(
            db,
            workspace_kind=workspace['kind'],
            user_id=user_id if workspace['kind'] == 'personal' else None,
            enterprise_id=workspace['enterprise_id'],
            app_id=app_id,
            status='active',
            enabled_by=user_id,
        )
        await db.flush()
        payload = _workspace_event_payload(row)
        await self.workbench_bus.publish('on_app_enabled', payload)
        return _workspace_app_payload(row)

    async def disable_current_workspace_app(self, db: AsyncSession, *, user_id: int, app_id: str) -> dict[str, Any]:
        try:
            app = workbench_app_registry.get(app_id)
        except KeyError as exc:
            raise errors.NotFoundError(msg='工作台应用不存在') from exc
        workspace = await self.get_active_workspace(db, user_id=user_id)
        if workspace['kind'] == 'personal' and app.install_policy == 'auto':
            raise errors.RequestError(msg='auto_installed_personal_app_cannot_be_disabled')
        row = await self._get_workspace_app(
            db,
            workspace_kind=workspace['kind'],
            user_id=user_id if workspace['kind'] == 'personal' else None,
            enterprise_id=workspace['enterprise_id'],
            app_id=app_id,
        )
        if row is None:
            raise errors.NotFoundError(msg='工作空间应用不存在')
        row.status = 'disabled'
        await db.flush()
        payload = _workspace_event_payload(row)
        await self.workbench_bus.publish('on_app_disabled', payload)
        return _workspace_app_payload(row)

    # RF-CLOUD：数据面方法（list/create datasets、search、upload）已删除。
    # 知识库浏览/检索/上传现由 hasn-node daemon 经 KnowledgeAdapter 直连 RagFlow
    # （控制面/数据面分离，设计 §4.5）；云端 service 只保留凭据下发 + 企业实例配置。

    async def get_enterprise_ragflow_instance(
        self, db: AsyncSession, *, enterprise_id: int, user_id: int
    ) -> dict[str, Any]:
        await self._require_enterprise_knowledge_admin(db, enterprise_id=enterprise_id, user_id=user_id)
        instance = await _scalar(
            db,
            sa.select(HasnAppInstance).where(
                HasnAppInstance.app_id == KNOWLEDGE_APP_ID,
                HasnAppInstance.scope == 'enterprise',
                HasnAppInstance.enterprise_id == enterprise_id,
            ),
        )
        if instance is None:
            return {'enterprise_id': enterprise_id, 'status': 'pending_config'}
        return _ragflow_instance_payload(instance)

    async def save_enterprise_ragflow_instance(
        self,
        db: AsyncSession,
        *,
        enterprise_id: int,
        user_id: int,
        url: str,
        admin_api_key: str,
        public_pem: str,
        default_embd_id: str | None = None,
        default_llm_id: str | None = None,
    ) -> dict[str, Any]:
        await self._require_enterprise_knowledge_admin(db, enterprise_id=enterprise_id, user_id=user_id)
        instance = await _scalar(
            db,
            sa.select(HasnAppInstance).where(
                HasnAppInstance.app_id == KNOWLEDGE_APP_ID,
                HasnAppInstance.scope == 'enterprise',
                HasnAppInstance.enterprise_id == enterprise_id,
            ),
        )
        config = _knowledge_instance_config(
            public_pem=public_pem, default_embd_id=default_embd_id, default_llm_id=default_llm_id
        )
        if instance is None:
            instance = HasnAppInstance(
                app_id=KNOWLEDGE_APP_ID,
                scope='enterprise',
                enterprise_id=enterprise_id,
                endpoint=url or None,
                transport_default='daemon_direct',
                credential_ref=key_encryption.encrypt(admin_api_key) if admin_api_key else '',
                status='active',
                config=config,
            )
            db.add(instance)
        else:
            instance.endpoint = url or None
            if admin_api_key:
                instance.credential_ref = key_encryption.encrypt(admin_api_key)
            instance.transport_default = 'daemon_direct'
            instance.status = 'active'
            instance.config = config
        await db.flush()
        await db.refresh(instance)
        return _ragflow_instance_payload(instance)

    async def test_enterprise_ragflow_instance(
        self, db: AsyncSession, *, enterprise_id: int, user_id: int
    ) -> dict[str, Any]:
        instance = await self.get_enterprise_ragflow_instance(db, enterprise_id=enterprise_id, user_id=user_id)
        return {'enterprise_id': enterprise_id, 'ok': instance.get('status') == 'active'}

    async def disable_enterprise_ragflow_instance(
        self, db: AsyncSession, *, enterprise_id: int, user_id: int
    ) -> dict[str, Any]:
        await self._require_enterprise_knowledge_admin(db, enterprise_id=enterprise_id, user_id=user_id)
        instance = await _scalar(
            db,
            sa.select(HasnAppInstance).where(
                HasnAppInstance.app_id == KNOWLEDGE_APP_ID,
                HasnAppInstance.scope == 'enterprise',
                HasnAppInstance.enterprise_id == enterprise_id,
            ),
        )
        if instance is None:
            raise errors.NotFoundError(msg='知识库服务配置不存在')
        instance.status = 'disabled'
        await db.flush()
        return _ragflow_instance_payload(instance)

    # RF-CLOUD：数据面热路径（_active_knowledge_context / _default_dataset_id /
    # _call_ragflow_with_refresh）已随数据面方法一并删除——云端不再直连 RagFlow
    # 数据面，凭据下发后由 hasn-node daemon 持 api_key 直接检索。

    async def _require_enterprise_knowledge_admin(self, db: AsyncSession, *, enterprise_id: int, user_id: int) -> None:
        enterprise = await self._get_enterprise_model(db, enterprise_id)
        if enterprise.owner_user_id == user_id:
            return

        membership = await self._approved_membership(db, enterprise_id=enterprise_id, user_id=user_id)
        if membership is not None and membership.role in {'owner', 'admin'}:
            return

        raise errors.ForbiddenError(msg='仅企业所有者或管理员可管理知识库服务配置')

    async def _get_enterprise_model(self, db: AsyncSession, enterprise_id: int):
        enterprise = await _scalar(db, sa.select(HasnEnterprise).where(HasnEnterprise.id == enterprise_id))
        if enterprise is None:
            raise errors.NotFoundError(msg='企业不存在')
        return enterprise

    async def _get_membership_model(self, db: AsyncSession, *, enterprise_id: int, membership_id: int):
        membership = await _scalar(
            db,
            sa.select(HasnEnterpriseMembership).where(
                HasnEnterpriseMembership.id == membership_id,
                HasnEnterpriseMembership.enterprise_id == enterprise_id,
            ),
        )
        if membership is None:
            raise errors.NotFoundError(msg='企业申请不存在')
        return membership

    async def _approved_membership(self, db: AsyncSession, *, enterprise_id: int | None, user_id: int):
        if enterprise_id is None:
            return None
        return await _scalar(
            db,
            sa.select(HasnEnterpriseMembership).where(
                HasnEnterpriseMembership.enterprise_id == enterprise_id,
                HasnEnterpriseMembership.user_id == user_id,
                HasnEnterpriseMembership.status == 'approved',
            ),
        )

    async def _set_active_workspace(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        kind: str,
        enterprise_id: int | None,
    ) -> dict[str, Any]:
        active = await _scalar(
            db,
            sa.select(HasnUserActiveWorkspace).where(HasnUserActiveWorkspace.user_id == user_id),
        )
        if active is None:
            db.add(HasnUserActiveWorkspace(user_id=user_id, kind=kind, enterprise_id=enterprise_id))
        else:
            active.kind = kind
            active.enterprise_id = enterprise_id
            if hasattr(active, 'switched_at'):
                active.switched_at = timezone.now()
        return {'kind': kind, 'enterprise_id': enterprise_id}

    async def _fallback_to_personal_if_active(self, db: AsyncSession, *, user_id: int, enterprise_id: int) -> None:
        active = await _scalar(
            db,
            sa.select(HasnUserActiveWorkspace).where(
                HasnUserActiveWorkspace.user_id == user_id,
                HasnUserActiveWorkspace.kind == 'enterprise',
                HasnUserActiveWorkspace.enterprise_id == enterprise_id,
            ),
        )
        if active is None:
            return
        prev = {'kind': 'enterprise', 'enterprise_id': enterprise_id}
        active.kind = 'personal'
        active.enterprise_id = None
        if hasattr(active, 'switched_at'):
            active.switched_at = timezone.now()
        await self.enterprise_bus.publish(
            'on_workspace_switched',
            {'user_id': user_id, 'prev_workspace': prev, 'next_workspace': {'kind': 'personal', 'enterprise_id': None}},
        )

    async def _workspace_app_rows(self, db: AsyncSession, *, workspace: dict[str, Any], user_id: int):
        stmt = sa.select(HasnWorkspaceApp)
        if workspace['kind'] == 'personal':
            stmt = stmt.where(HasnWorkspaceApp.workspace_kind == 'personal', HasnWorkspaceApp.user_id == user_id)
        else:
            stmt = stmt.where(
                HasnWorkspaceApp.workspace_kind == 'enterprise',
                HasnWorkspaceApp.enterprise_id == workspace['enterprise_id'],
            )
        return (await db.execute(stmt.order_by(HasnWorkspaceApp.id.asc()))).scalars().all()

    async def _get_workspace_app(
        self,
        db: AsyncSession,
        *,
        workspace_kind: str,
        user_id: int | None,
        enterprise_id: int | None,
        app_id: str,
    ):
        stmt = sa.select(HasnWorkspaceApp).where(
            HasnWorkspaceApp.workspace_kind == workspace_kind,
            HasnWorkspaceApp.app_id == app_id,
        )
        if workspace_kind == 'personal':
            stmt = stmt.where(HasnWorkspaceApp.user_id == user_id)
        else:
            stmt = stmt.where(HasnWorkspaceApp.enterprise_id == enterprise_id)
        return await _scalar(db, stmt)

    async def _upsert_workspace_app(
        self,
        db: AsyncSession,
        *,
        workspace_kind: str,
        user_id: int | None,
        enterprise_id: int | None,
        app_id: str,
        status: str,
        enabled_by: int | None,
    ):
        if app_id not in {app.id for app in workbench_app_registry.list(workspace_kind)}:
            raise errors.NotFoundError(msg='工作台应用不存在')
        row = await self._get_workspace_app(
            db,
            workspace_kind=workspace_kind,
            user_id=user_id,
            enterprise_id=enterprise_id,
            app_id=app_id,
        )
        if row is None:
            row = HasnWorkspaceApp(
                workspace_kind=workspace_kind,
                user_id=user_id,
                enterprise_id=enterprise_id,
                app_id=app_id,
                status=status,
                config={},
                enabled_by=enabled_by,
            )
            db.add(row)
            await db.flush()
            await db.refresh(row)
        else:
            row.status = status
            row.enabled_by = enabled_by
        return row

    async def _resolve_knowledge_instance(self, db: AsyncSession, *, workspace: dict[str, Any]):
        """经通用 instance_resolver 解析知识库实例（实施 03 P4：删知识库专用解析分支，复用 resolve）。

        与第三方 App 共用同一条 resolve()（企业 active → 企业实例；否则回落公共）。face='ui' → daemon_direct。
        传入 builtin manifest 跳过 manifest 的 DB 查询；resolve 只返回 active 实例，故返回非 None 即 active。
        返回完整 ORM 行（凭据查询要 instance.id、序列化要 config/endpoint/status）。
        """
        try:
            handle = await instance_resolver.resolve(
                db,
                app_id=KNOWLEDGE_APP_ID,
                workspace=workspace,
                face=FACE_UI,
                manifest=ai_native_app_registry.get_builtin_manifest(KNOWLEDGE_APP_ID),
            )
        except InstanceResolutionError:
            return None
        if handle.instance_id is None:
            return None
        return await db.get(HasnAppInstance, int(handle.instance_id))


async def _scalar(db: AsyncSession, stmt):
    return (await db.execute(stmt)).scalar_one_or_none()


async def _grouped_count(db: AsyncSession, stmt: Any) -> dict[int, int]:
    rows = (await db.execute(stmt)).all()
    return {int(key): int(value or 0) for key, value in rows if key is not None}


def _empty_workspace_stats() -> dict[str, int]:
    return {'member_count': 0, 'app_count': 0, 'admin_count': 0}


_ENTERPRISE_COMMON_SUFFIXES = (
    '有限责任公司',
    '股份有限公司',
    '集团有限公司',
    '有限公司',
    '责任公司',
    '集团公司',
    '控股集团',
    '集团',
    '公司',
    '企业',
    '工作室',
)


def _strip_enterprise_common_suffix(name: str) -> str:
    value = re.sub(r'\s+', '', name.strip())
    changed = True
    while changed:
        changed = False
        for suffix in _ENTERPRISE_COMMON_SUFFIXES:
            if value.endswith(suffix) and len(value) > len(suffix):
                value = value[: -len(suffix)]
                changed = True
                break
    return value or name.strip()


def _enterprise_slug_base(name: str) -> str:
    value = _strip_enterprise_common_suffix(name)
    pieces: list[str] = []
    for char in value:
        if char.isascii() and char.isalnum():
            pieces.append(char.lower())
            continue
        initials = lazy_pinyin(char, style=Style.FIRST_LETTER, errors='ignore')
        if initials:
            initial = initials[0].lower()
            if initial.isascii() and initial.isalnum():
                pieces.append(initial)
    slug = ''.join(pieces).strip('-')[:64]
    return slug or 'enterprise'


async def _generate_unique_enterprise_slug(db: AsyncSession, name: str) -> str:
    base = _enterprise_slug_base(name)
    index = 1
    while True:
        suffix = '' if index == 1 else f'-{index}'
        candidate = f'{base[: 64 - len(suffix)]}{suffix}'
        existing = await _scalar(db, sa.select(HasnEnterprise.id).where(HasnEnterprise.slug == candidate))
        if existing is None:
            return candidate
        index += 1


def _datetime_payload(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)


def _enterprise_payload(enterprise) -> dict[str, Any]:
    return {
        'id': enterprise.id,
        'name': enterprise.name,
        'slug': enterprise.slug,
        'logo': getattr(enterprise, 'logo', None),
        'industry': getattr(enterprise, 'industry', None),
        'company_size': getattr(enterprise, 'company_size', None),
        'description': getattr(enterprise, 'description', None),
        'owner_user_id': enterprise.owner_user_id,
        'join_policy': enterprise.join_policy,
        'status': enterprise.status,
        'created_at': _datetime_payload(getattr(enterprise, 'created_at', None)),
        'updated_at': _datetime_payload(getattr(enterprise, 'updated_at', None)),
    }


def _membership_payload(membership, user=None) -> dict[str, Any]:
    return {
        'id': membership.id,
        'enterprise_id': membership.enterprise_id,
        'user_id': membership.user_id,
        'nickname': getattr(user, 'nickname', None),
        'phone': getattr(user, 'phone', None),
        'role': membership.role,
        'status': membership.status,
        'apply_message': membership.apply_message,
        'apply_via': membership.apply_via,
        'invite_code': membership.invite_code,
        'decided_by': membership.decided_by,
        'decision_note': membership.decision_note,
    }


def _invite_payload(invite) -> dict[str, Any]:
    return {
        'id': invite.id,
        'enterprise_id': invite.enterprise_id,
        'code': invite.code,
        'created_by': invite.created_by,
        'max_uses': invite.max_uses,
        'used_count': invite.used_count,
        'expires_at': invite.expires_at,
        'auto_approve': invite.auto_approve,
        'revoked': invite.revoked,
    }


def _workspace_app_payload(row) -> dict[str, Any]:
    return {
        'id': row.id,
        'workspace_kind': row.workspace_kind,
        'user_id': row.user_id,
        'enterprise_id': row.enterprise_id,
        'app_id': row.app_id,
        'status': row.status,
        'config': row.config,
        'enabled_by': row.enabled_by,
    }


def _workspace_event_payload(row) -> dict[str, Any]:
    return {
        'workspace_kind': row.workspace_kind,
        'user_id': row.user_id,
        'enterprise_id': row.enterprise_id,
        'app_id': row.app_id,
    }


def _knowledge_instance_config(
    *, public_pem: str | None, default_embd_id: str | None, default_llm_id: str | None
) -> dict[str, Any]:
    """RAGFlow 实例私有字段 → hasn_app_instance.config（实施 03 §2.2，空值不落 config）。"""
    config: dict[str, Any] = {}
    if public_pem:
        config['public_pem'] = public_pem
    if default_embd_id:
        config['default_embd_id'] = default_embd_id
    if default_llm_id:
        config['default_llm_id'] = default_llm_id
    return config


def _ragflow_instance_payload(instance) -> dict[str, Any]:
    # 收编后底层是 hasn_app_instance(app_id='knowledge')，RAGFlow 私有字段在 config；
    # 输出字段名保持不变以守住 daemon /knowledge/credentials* 与 webui 企业实例配置契约（实施 03 §5）。
    config = instance.config or {}
    return {
        'id': getattr(instance, 'id', None),
        'scope': instance.scope,
        'enterprise_id': instance.enterprise_id,
        'url': instance.endpoint or '',
        'admin_api_key_encrypted': 'stored' if instance.credential_ref else None,
        'public_pem': config.get('public_pem', ''),
        'default_embd_id': config.get('default_embd_id'),
        'default_llm_id': config.get('default_llm_id'),
        'status': instance.status,
    }


# RF-CLOUD：RagFlow 数据面响应解析助手（_ragflow_auth_headers / _ragflow_data_list /
# _ragflow_documents / _document_payload / _search_chunk_payload）已删除——云端不再
# 解析 RagFlow 检索/文档响应，该职责下沉至 hasn-node daemon 的 KnowledgeAdapter。


workbench_domain_service = WorkbenchDomainService()

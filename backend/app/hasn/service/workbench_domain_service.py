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
    HasnEnterpriseMemberRole,
    HasnEnterpriseMembership,
    HasnEnterpriseRole,
    HasnHumans,
)
from backend.app.admin.model.user import User
from backend.app.workbench.model import HasnOwnerWorkbenchPref
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
        # 应用平台 v3 P3（设计 17 决策①）：应用一律开箱即用，挂载概念废除
        # （`hasn_workspace_app` 退役）——建企业不再 ensure_auto_apps 写挂载行。
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
        # 附 hasn_id（join HasnHumans）：获客 GE5「分配负责人」需按成员 hasn_id 设 assignee。
        members = (
            (
                await db.execute(
                    sa
                    .select(HasnEnterpriseMembership, User, HasnHumans.hasn_id)
                    .outerjoin(User, User.id == HasnEnterpriseMembership.user_id)
                    .outerjoin(HasnHumans, HasnHumans.user_id == HasnEnterpriseMembership.user_id)
                    .where(HasnEnterpriseMembership.enterprise_id == enterprise_id)
                    .order_by(HasnEnterpriseMembership.id.asc())
                )
            )
            .all()
        )
        return {
            'items': [
                _membership_payload(member, user, hasn_id=hasn_id) for member, user, hasn_id in members
            ],
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

    # ── 企业角色 / 部门管理（应用平台 v3 P3 §4.2(4)/§6.5）──────────────────────
    # 仅企业 owner / admin 可管理本企业角色；跨企业隔离：role / member_role 行均带
    # enterprise_id，所有读写都按 enterprise_id 限定，绝不跨企业串。

    async def _require_enterprise_admin(
        self, db: AsyncSession, *, enterprise_id: int, user_id: int, action: str
    ) -> None:
        enterprise = await self._get_enterprise_model(db, enterprise_id)
        if enterprise.owner_user_id == user_id:
            return
        membership = await self._approved_membership(db, enterprise_id=enterprise_id, user_id=user_id)
        if membership is not None and membership.role in {'owner', 'admin'}:
            return
        raise errors.ForbiddenError(msg=f'仅企业所有者或管理员可{action}')

    async def _get_role_model(self, db: AsyncSession, *, enterprise_id: int, role_id: int) -> HasnEnterpriseRole:
        role = await _scalar(
            db,
            sa.select(HasnEnterpriseRole).where(
                HasnEnterpriseRole.id == role_id,
                HasnEnterpriseRole.enterprise_id == enterprise_id,
            ),
        )
        if role is None:
            raise errors.NotFoundError(msg='角色 / 部门不存在')
        return role

    async def list_roles(self, db: AsyncSession, *, enterprise_id: int, operator_user_id: int) -> dict[str, Any]:
        await self._require_enterprise_admin(
            db, enterprise_id=enterprise_id, user_id=operator_user_id, action='查看企业角色'
        )
        roles = (
            (
                await db.execute(
                    sa.select(HasnEnterpriseRole)
                    .where(HasnEnterpriseRole.enterprise_id == enterprise_id)
                    .order_by(HasnEnterpriseRole.id.asc())
                )
            )
            .scalars()
            .all()
        )
        member_counts = await _grouped_count(
            db,
            sa.select(HasnEnterpriseMemberRole.role_id, sa.func.count())
            .where(HasnEnterpriseMemberRole.enterprise_id == enterprise_id)
            .group_by(HasnEnterpriseMemberRole.role_id),
        )
        return {
            'enterprise_id': enterprise_id,
            'items': [_role_payload(role, member_count=member_counts.get(role.id, 0)) for role in roles],
        }

    async def create_role(
        self, db: AsyncSession, *, enterprise_id: int, operator_user_id: int, name: str, kind: str = 'role'
    ) -> dict[str, Any]:
        await self._require_enterprise_admin(
            db, enterprise_id=enterprise_id, user_id=operator_user_id, action='创建企业角色'
        )
        clean_name = (name or '').strip()
        if not clean_name:
            raise errors.RequestError(msg='角色 / 部门名称不能为空')
        if len(clean_name) > 64:
            raise errors.RequestError(msg='角色 / 部门名称不能超过 64 个字符')
        if kind not in {'role', 'department'}:
            raise errors.RequestError(msg='kind 只能是 role 或 department')
        existing = await _scalar(
            db,
            sa.select(HasnEnterpriseRole.id).where(
                HasnEnterpriseRole.enterprise_id == enterprise_id,
                HasnEnterpriseRole.name == clean_name,
            ),
        )
        if existing is not None:
            raise errors.ConflictError(msg='同名角色 / 部门已存在')
        role = HasnEnterpriseRole(enterprise_id=enterprise_id, name=clean_name, kind=kind)
        db.add(role)
        await db.flush()
        await db.refresh(role)
        return _role_payload(role, member_count=0)

    async def update_role(
        self,
        db: AsyncSession,
        *,
        enterprise_id: int,
        operator_user_id: int,
        role_id: int,
        name: str | None = None,
        kind: str | None = None,
    ) -> dict[str, Any]:
        await self._require_enterprise_admin(
            db, enterprise_id=enterprise_id, user_id=operator_user_id, action='修改企业角色'
        )
        role = await self._get_role_model(db, enterprise_id=enterprise_id, role_id=role_id)
        if name is not None:
            clean_name = name.strip()
            if not clean_name:
                raise errors.RequestError(msg='角色 / 部门名称不能为空')
            if len(clean_name) > 64:
                raise errors.RequestError(msg='角色 / 部门名称不能超过 64 个字符')
            if clean_name != role.name:
                duplicate = await _scalar(
                    db,
                    sa.select(HasnEnterpriseRole.id).where(
                        HasnEnterpriseRole.enterprise_id == enterprise_id,
                        HasnEnterpriseRole.name == clean_name,
                        HasnEnterpriseRole.id != role_id,
                    ),
                )
                if duplicate is not None:
                    raise errors.ConflictError(msg='同名角色 / 部门已存在')
                role.name = clean_name
        if kind is not None:
            if kind not in {'role', 'department'}:
                raise errors.RequestError(msg='kind 只能是 role 或 department')
            role.kind = kind
        role.updated_time = timezone.now()
        await db.flush()
        member_count = await _scalar(
            db,
            sa.select(sa.func.count())
            .select_from(HasnEnterpriseMemberRole)
            .where(HasnEnterpriseMemberRole.role_id == role_id),
        )
        return _role_payload(role, member_count=int(member_count or 0))

    async def delete_role(self, db: AsyncSession, *, enterprise_id: int, operator_user_id: int, role_id: int) -> None:
        await self._require_enterprise_admin(
            db, enterprise_id=enterprise_id, user_id=operator_user_id, action='删除企业角色'
        )
        role = await self._get_role_model(db, enterprise_id=enterprise_id, role_id=role_id)
        # 无 FK 级联：先按 enterprise_id + role_id 清成员关联，再删角色本身。
        await db.execute(
            sa.delete(HasnEnterpriseMemberRole).where(
                HasnEnterpriseMemberRole.enterprise_id == enterprise_id,
                HasnEnterpriseMemberRole.role_id == role_id,
            )
        )
        await db.delete(role)
        await db.flush()

    async def list_role_members(
        self, db: AsyncSession, *, enterprise_id: int, operator_user_id: int, role_id: int
    ) -> dict[str, Any]:
        await self._require_enterprise_admin(
            db, enterprise_id=enterprise_id, user_id=operator_user_id, action='查看企业角色成员'
        )
        await self._get_role_model(db, enterprise_id=enterprise_id, role_id=role_id)
        rows = (
            await db.execute(
                sa.select(HasnEnterpriseMemberRole, User)
                .join(User, User.id == HasnEnterpriseMemberRole.user_id, isouter=True)
                .where(
                    HasnEnterpriseMemberRole.enterprise_id == enterprise_id,
                    HasnEnterpriseMemberRole.role_id == role_id,
                )
                .order_by(HasnEnterpriseMemberRole.id.asc())
            )
        ).all()
        return {
            'enterprise_id': enterprise_id,
            'role_id': role_id,
            'items': [
                {
                    'user_id': mr.user_id,
                    'nickname': getattr(user, 'nickname', None),
                    'phone': getattr(user, 'phone', None),
                    'assigned_at': _datetime_payload(getattr(mr, 'created_time', None)),
                }
                for mr, user in rows
            ],
        }

    async def grant_member_role(
        self, db: AsyncSession, *, enterprise_id: int, operator_user_id: int, role_id: int, user_id: int
    ) -> dict[str, Any]:
        await self._require_enterprise_admin(
            db, enterprise_id=enterprise_id, user_id=operator_user_id, action='授予企业角色'
        )
        await self._get_role_model(db, enterprise_id=enterprise_id, role_id=role_id)
        # 仅可授予本企业 approved 成员，不能给非成员挂角色（跨企业隔离的第二道闸）。
        membership = await self._approved_membership(db, enterprise_id=enterprise_id, user_id=user_id)
        if membership is None:
            raise errors.RequestError(msg='该用户不是本企业成员')
        existing = await _scalar(
            db,
            sa.select(HasnEnterpriseMemberRole.id).where(
                HasnEnterpriseMemberRole.role_id == role_id,
                HasnEnterpriseMemberRole.user_id == user_id,
            ),
        )
        if existing is None:
            db.add(HasnEnterpriseMemberRole(enterprise_id=enterprise_id, user_id=user_id, role_id=role_id))
            await db.flush()
        return {'enterprise_id': enterprise_id, 'role_id': role_id, 'user_id': user_id, 'granted': True}

    async def revoke_member_role(
        self, db: AsyncSession, *, enterprise_id: int, operator_user_id: int, role_id: int, user_id: int
    ) -> None:
        await self._require_enterprise_admin(
            db, enterprise_id=enterprise_id, user_id=operator_user_id, action='撤销企业角色'
        )
        await self._get_role_model(db, enterprise_id=enterprise_id, role_id=role_id)
        await db.execute(
            sa.delete(HasnEnterpriseMemberRole).where(
                HasnEnterpriseMemberRole.enterprise_id == enterprise_id,
                HasnEnterpriseMemberRole.role_id == role_id,
                HasnEnterpriseMemberRole.user_id == user_id,
            )
        )
        await db.flush()

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
        # 应用平台 v3 P3（设计 17 决策①）：挂载废除，app_count = 该空间「开箱即用」的已发布
        # 应用数（catalog kind=personal），不再数 `hasn_workspace_app` 挂载行。
        app_count = await self._published_app_count(db, kind='personal')
        return {
            'member_count': 1,
            'app_count': app_count,
            'admin_count': 1,
        }

    async def _published_app_count(self, db: AsyncSession, *, kind: str) -> int:
        """该 workspace_kind 下已发布应用数（开箱即用，同 kind 所有空间一致）。"""
        return len(await app_catalog_service.list_published_catalog(db, kind=kind))

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
        # 应用平台 v3 P3（设计 17 决策①）：挂载废除，企业 app_count = 已发布企业应用数
        # （开箱即用，同 kind 所有企业一致），不再按 `hasn_workspace_app` 分组数挂载行。
        app_count = await self._published_app_count(db, kind='enterprise')

        return {
            enterprise_id: {
                'member_count': member_counts.get(enterprise_id, 0),
                'app_count': app_count,
                'admin_count': admin_counts.get(enterprise_id, 0),
            }
            for enterprise_id in enterprise_ids
        }

    async def _pref_row(self, db: AsyncSession, *, owner_hasn_id: str) -> HasnOwnerWorkbenchPref | None:
        """取 owner 的工作台偏好行（每人一行）；不存在返回 None。"""
        return await _scalar(
            db,
            sa.select(HasnOwnerWorkbenchPref).where(HasnOwnerWorkbenchPref.owner_hasn_id == owner_hasn_id),
        )

    async def get_active_workspace(self, db: AsyncSession, *, user_id: int) -> dict[str, Any]:
        # 应用平台 v3 P3：身份上下文从 hasn_user_active_workspace（已退役）改读
        # hasn_owner_workbench_pref.active_enterprise_id 瘦指针（设计 17 §4.2(1)）。
        # 返回契约 {kind, enterprise_id} 保持不变（kind 派生：null→personal，非 null→enterprise），
        # 8 个消费者无需改动。
        owner_hasn_id = await app_catalog_service.resolve_owner_hasn_id(db, user_id=user_id)
        if not owner_hasn_id:
            return {'kind': 'personal', 'enterprise_id': None}
        pref = await self._pref_row(db, owner_hasn_id=owner_hasn_id)
        enterprise_id = pref.active_enterprise_id if pref is not None else None
        if enterprise_id is None:
            return {'kind': 'personal', 'enterprise_id': None}
        # 自愈：active_enterprise 已失去 approved 成员资格（被移除 / 企业停用）→ 复位个人。
        membership = await self._approved_membership(db, enterprise_id=enterprise_id, user_id=user_id)
        if membership is None:
            if pref is not None:
                pref.active_enterprise_id = None
            return {'kind': 'personal', 'enterprise_id': None}
        return {'kind': 'enterprise', 'enterprise_id': enterprise_id}

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
        next_workspace = await self._set_active_enterprise(
            db,
            user_id=user_id,
            enterprise_id=enterprise_id if kind == 'enterprise' else None,
        )
        await db.flush()
        await self.enterprise_bus.publish(
            'on_workspace_switched',
            {'user_id': user_id, 'prev_workspace': prev, 'next_workspace': next_workspace},
        )
        return next_workspace

    # 应用平台 v3 P3（设计 17 决策①）：挂载概念废除（`hasn_workspace_app` 退役），
    # ensure_auto_apps / list_current_workspace_apps（"已挂载应用"）已删除——应用一律开箱即用，
    # 展示目录由 list_workbench_apps（catalog ∩ entitlement）权威给出。

    async def list_workbench_apps(
        self, db: AsyncSession, *, user_id: int, workspace_kind: str | None = None
    ) -> list[dict[str, Any]]:
        workspace = await self.get_active_workspace(db, user_id=user_id)
        effective_kind = workspace_kind or workspace['kind']
        # 防御性幂等播种：生产由启动期 reconcile 保证已 seed（此处仅一次存在性 SELECT、零写）；
        # 兜底未 seed 环境（测试 / seed 失败）也能返回内置应用，不破坏工作台。
        await app_catalog_service.ensure_catalog_seeded(db)
        # 应用平台 v3 P2（entitlement 收口，开箱即用，设计 17 §6.1）：展示目录 =
        # **catalog(published) ∩ entitlement**，**不再叠加 per-workspace 挂载态**——挂载概念
        # 废除（`hasn_workspace_app` 退役，P3/P4 删表），已发布应用对 owner 恒 `available`，
        # 是否可用纯由准入决定（免费恒真 / 付费走 `resolve_app_access`，§5.2）。
        # C2：catalog（DB 权威）取代硬编码 registry 作为展示目录来源（设计 §6.3）。
        # launch 字段（ui_kind/window_url/window_origin）迁移期仍从本地 registry overlay，
        # registry 在 C6 退役后由 daemon 本地提供（设计 §3 边界）。
        reg_by_id = {a.id: a for a in workbench_app_registry.list()}
        # C4 闸门①：每行附 access（§5.2）。owner 维度准入用 owner hasn_id（tier/purchase 实时判定）。
        owner_hasn_id = await app_catalog_service.resolve_owner_hasn_id(db, user_id=user_id)
        apps = []
        for cat in await app_catalog_service.list_published_catalog(db, kind=effective_kind):
            manifest = app_catalog_service.catalog_to_manifest(cat, registry_app=reg_by_id.get(cat.app_id))
            manifest['status'] = 'available'
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

    # 应用平台 v3 P3（设计 17 决策①）：enable/disable_current_workspace_app（挂载/卸载）已删除。
    # 应用一律开箱即用，是否可用纯由商业化准入决定（list_workbench_apps 每行附 access，
    # 付费走 resolve_app_access，§5.2）；不再有"挂载开关"写 `hasn_workspace_app`。

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

    async def _set_active_enterprise(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        enterprise_id: int | None,
    ) -> dict[str, Any]:
        # 应用平台 v3 P3：身份上下文写 hasn_owner_workbench_pref.active_enterprise_id 瘦指针
        # （owner-scoped 每人一行）。需 owner_hasn_id 定位偏好行；无 hasn_humans（owner_hasn_id 缺）
        # 的用户只能是个人上下文，无法持久化企业指针——返回派生结果，不静默造行。
        owner_hasn_id = await app_catalog_service.resolve_owner_hasn_id(db, user_id=user_id)
        kind = 'enterprise' if enterprise_id is not None else 'personal'
        if not owner_hasn_id:
            return {'kind': kind, 'enterprise_id': enterprise_id}
        pref = await self._pref_row(db, owner_hasn_id=owner_hasn_id)
        if pref is None:
            db.add(HasnOwnerWorkbenchPref(owner_hasn_id=owner_hasn_id, active_enterprise_id=enterprise_id))
        else:
            pref.active_enterprise_id = enterprise_id
        return {'kind': kind, 'enterprise_id': enterprise_id}

    async def _fallback_to_personal_if_active(self, db: AsyncSession, *, user_id: int, enterprise_id: int) -> None:
        # 成员被移除 / 主动退出企业时，若该企业正是当前上下文 → 复位个人（清 active_enterprise_id）。
        owner_hasn_id = await app_catalog_service.resolve_owner_hasn_id(db, user_id=user_id)
        if not owner_hasn_id:
            return
        pref = await self._pref_row(db, owner_hasn_id=owner_hasn_id)
        if pref is None or pref.active_enterprise_id != enterprise_id:
            return
        pref.active_enterprise_id = None
        await self.enterprise_bus.publish(
            'on_workspace_switched',
            {
                'user_id': user_id,
                'prev_workspace': {'kind': 'enterprise', 'enterprise_id': enterprise_id},
                'next_workspace': {'kind': 'personal', 'enterprise_id': None},
            },
        )

    # 应用平台 v3 P3（设计 17 决策①）：挂载行私有读写助手
    # （_workspace_app_rows / _get_workspace_app / _upsert_workspace_app）随 `hasn_workspace_app`
    # 退役一并删除。

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


def _membership_payload(membership, user=None, *, hasn_id: str | None = None) -> dict[str, Any]:
    return {
        'id': membership.id,
        'enterprise_id': membership.enterprise_id,
        'user_id': membership.user_id,
        'hasn_id': hasn_id,
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


def _role_payload(role, *, member_count: int = 0) -> dict[str, Any]:
    return {
        'id': role.id,
        'enterprise_id': role.enterprise_id,
        'name': role.name,
        'kind': role.kind,
        'member_count': member_count,
        'created_at': _datetime_payload(getattr(role, 'created_time', None)),
        'updated_at': _datetime_payload(getattr(role, 'updated_time', None)),
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

"""通用产物共享服务（平台级，应用平台 v3 §6.3/§6.5）。

一张 `hasn_resource_share` 表管所有文档型 AI-Native 应用（deck/doc/个人知识库…）的显式协作授权，
配合产物自身的 `visibility` 快路径，给出「主体 S 对产物 R 的有效 permission」。

判定算法（§6.5）：effective(S,R) = max(owner_grant, admin_grant, visibility_grant, explicit_grant)
- owner_grant     : S 的主人 == R 的主人 → manager（分身代主人也算）
- admin_grant     : R 属企业且 S 的主人是该企业 owner/admin → manager（决策③ 离职兜底）
- visibility_grant: R.visibility=enterprise 且 S 的主人是该企业成员 → viewer；R.visibility=link → viewer
- explicit_grant  : hasn_resource_share 命中 S（直接 / 经其主人 / 经其企业 / 经 role[P3]）的最高 active 权限

业务系统型应用（获客/CRM）不走本服务（走企业租户 + 角色裁剪），见 §6.7。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import and_, or_, select

from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
from backend.app.hasn.model import HasnEnterpriseMemberRole, HasnEnterpriseMembership, HasnResourceShare
from backend.app.hasn.service.authz.resource_registry import resource_kind_registry
from backend.common.exception import errors
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 权限档位序（用于取 max）
_PERM_RANK = {'none': 0, 'viewer': 1, 'editor': 2, 'manager': 3}
_RANK_PERM = {v: k for k, v in _PERM_RANK.items()}
# 企业 owner/admin 视为「经理」角色（对企业产物有 manager 兜底；可见企业全部）
_ENTERPRISE_ADMIN_ROLES = ('owner', 'admin')
# 成员关系生效态
_MEMBERSHIP_ACTIVE = ('approved',)


def rank(permission: str | None) -> int:
    return _PERM_RANK.get(permission or 'none', 0)


@dataclass
class PermissionResolutionCache:
    """G6 门（doc32 §5.3）用的 per-request 判权记忆化缓存——**纯附加、纯性能**，不改判权语义。

    一次工具调用内 subject 恒定（多资源声明 + 父链复判共享同一主体）：memberships 与 role grantee
    在同一 subject 下每次查得**完全一致**，故按 key 记忆化省重复 SELECT。**不传（None）时行为逐字不变**，
    存量调用点全部走无缓存路径。缓存生命周期 = 一次 `resource_gate.enforce_declaration`，用完即弃。
    """

    # owner_hasn_id → [(enterprise_id, role)] 成员关系
    memberships: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    # (owner_hasn_id, resource_enterprise_id) → role grantee_id 集合
    role_grantees: dict[tuple[str, int | None], set[str]] = field(default_factory=dict)


def _max_perm(a: str, b: str) -> str:
    return a if rank(a) >= rank(b) else b


class ResourceShareService:
    """通用产物共享 + 有效权限判定。所有方法第一参数 db。"""

    # ---------- 企业成员解析 ----------

    @staticmethod
    async def acting_human_memberships(
        db: AsyncSession, human_hasn_id: str, *, cache: PermissionResolutionCache | None = None
    ) -> list[tuple[int, str]]:
        """主人 hasn_id → 其 approved 企业成员关系列表 [(enterprise_id, role)]。

        分身无独立企业身份，按其主人解析（分身代主人行动）。`cache` 非空时按 hasn_id 记忆化（G6 门用）。
        """
        if cache is not None and human_hasn_id in cache.memberships:
            return cache.memberships[human_hasn_id]
        human = await hasn_humans_dao.get_by_hasn_id(db, human_hasn_id)
        if human is None:
            result: list[tuple[int, str]] = []
        else:
            rows = (
                await db.execute(
                    select(HasnEnterpriseMembership.enterprise_id, HasnEnterpriseMembership.role).where(
                        HasnEnterpriseMembership.user_id == human.user_id,
                        HasnEnterpriseMembership.status.in_(_MEMBERSHIP_ACTIVE),
                    )
                )
            ).all()
            result = [(int(eid), role) for eid, role in rows]
        if cache is not None:
            cache.memberships[human_hasn_id] = result
        return result

    @staticmethod
    async def _role_grantee_ids(
        db: AsyncSession,
        *,
        subject_owner_hasn_id: str,
        resource_enterprise_id: int | None,
        memberships: list[tuple[int, str]],
        cache: PermissionResolutionCache | None = None,
    ) -> set[str]:
        """主体 S 命中的 role grantee_id 集合（仅当产物属某企业时有意义，§6.5/§4.2(4)）。

        - 内置成员角色：按 S 在 `resource_enterprise_id` 的成员 role 推导，含包含关系
          owner ⊇ admin ⊇ member（grantee_id = `builtin:owner|admin|member`）。
        - 自定义角色 / 部门：查 `hasn_enterprise_member_role`（user_id × enterprise_id），
          grantee_id = str(role_id)。

        `cache` 非空时按 (owner, enterprise_id) 记忆化（G6 门用，纯性能不改语义）。
        """
        if resource_enterprise_id is None:
            return set()
        cache_key = (subject_owner_hasn_id, resource_enterprise_id)
        if cache is not None and cache_key in cache.role_grantees:
            return cache.role_grantees[cache_key]
        ids: set[str] = set()
        # 内置成员角色（含包含关系）
        role_in_ent = next((role for eid, role in memberships if eid == resource_enterprise_id), None)
        if role_in_ent is not None:
            ids.add('builtin:member')
            if role_in_ent in _ENTERPRISE_ADMIN_ROLES:
                ids.add('builtin:admin')
            if role_in_ent == 'owner':
                ids.add('builtin:owner')
        # 自定义角色 / 部门
        human = await hasn_humans_dao.get_by_hasn_id(db, subject_owner_hasn_id)
        if human is not None:
            rows = (
                (
                    await db.execute(
                        select(HasnEnterpriseMemberRole.role_id).where(
                            HasnEnterpriseMemberRole.user_id == human.user_id,
                            HasnEnterpriseMemberRole.enterprise_id == resource_enterprise_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            ids.update(str(int(rid)) for rid in rows)
        if cache is not None:
            cache.role_grantees[cache_key] = ids
        return ids

    # ---------- 有效权限判定（§6.5） ----------

    @staticmethod
    async def resolve_effective_permission(
        db: AsyncSession,
        *,
        subject_hasn_id: str,
        subject_kind: str,  # 'human' | 'agent'
        subject_owner_hasn_id: str,  # 主体背后的主人（human 时 == subject_hasn_id）
        resource_type: str,
        resource_id: str,
        resource_owner_hasn_id: str,
        resource_owner_scope: str = 'personal',  # personal | enterprise
        resource_enterprise_id: int | None = None,
        resource_visibility: str = 'private',  # private | enterprise | link
        perm_cache: PermissionResolutionCache
        | None = None,  # G6：同请求内多资源判权的记忆化缓存（默认 None＝行为不变）
    ) -> str:
        """返回 'none'|'viewer'|'editor'|'manager'。"""
        effective = 'none'

        # 1. owner_grant：主人相同（分身代主人）→ manager
        if subject_owner_hasn_id and subject_owner_hasn_id == resource_owner_hasn_id:
            return 'manager'

        memberships = await ResourceShareService.acting_human_memberships(db, subject_owner_hasn_id, cache=perm_cache)
        member_enterprise_ids = {eid for eid, _ in memberships}
        admin_enterprise_ids = {eid for eid, role in memberships if role in _ENTERPRISE_ADMIN_ROLES}

        # 2. admin_grant：企业产物 + 主人是该企业 owner/admin → manager（离职兜底）
        if (
            resource_owner_scope == 'enterprise'
            and resource_enterprise_id is not None
            and resource_enterprise_id in admin_enterprise_ids
        ):
            return 'manager'

        # 3. visibility_grant
        if (
            resource_visibility == 'enterprise'
            and resource_enterprise_id is not None
            and resource_enterprise_id in member_enterprise_ids
        ) or resource_visibility == 'link':
            effective = _max_perm(effective, 'viewer')

        # 4. explicit_grant：hasn_resource_share 命中主体（直接/经主人/经企业/经 role）的最高 active 权限
        human_ids = {subject_owner_hasn_id}
        agent_ids = {subject_hasn_id} if subject_kind == 'agent' else set()
        role_grantee_ids = await ResourceShareService._role_grantee_ids(
            db,
            subject_owner_hasn_id=subject_owner_hasn_id,
            resource_enterprise_id=resource_enterprise_id,
            memberships=memberships,
            cache=perm_cache,
        )
        conds = []
        if human_ids:
            conds.append(and_(HasnResourceShare.grantee_type == 'human', HasnResourceShare.grantee_id.in_(human_ids)))
        if agent_ids:
            conds.append(and_(HasnResourceShare.grantee_type == 'agent', HasnResourceShare.grantee_id.in_(agent_ids)))
        if member_enterprise_ids:
            conds.append(
                and_(
                    HasnResourceShare.grantee_type == 'enterprise',
                    HasnResourceShare.grantee_id.in_([str(e) for e in member_enterprise_ids]),
                )
            )
        if role_grantee_ids:
            conds.append(
                and_(
                    HasnResourceShare.grantee_type == 'role',
                    HasnResourceShare.grantee_id.in_(role_grantee_ids),
                )
            )
        if conds:
            rows = (
                (
                    await db.execute(
                        select(HasnResourceShare.permission).where(
                            HasnResourceShare.resource_type == resource_type,
                            HasnResourceShare.resource_id == resource_id,
                            HasnResourceShare.status == 'active',
                            or_(*conds),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for perm in rows:
                effective = _max_perm(effective, perm)

        return effective

    # ---------- 共享名单管理 ----------

    @staticmethod
    async def list_shares(db: AsyncSession, *, resource_type: str, resource_id: str) -> list[dict]:
        """某产物当前全部 active 共享行（共享面板用）。"""
        rows = (
            (
                await db.execute(
                    select(HasnResourceShare)
                    .where(
                        HasnResourceShare.resource_type == resource_type,
                        HasnResourceShare.resource_id == resource_id,
                        HasnResourceShare.status == 'active',
                    )
                    .order_by(HasnResourceShare.id.asc())
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                'id': r.id,
                'grantee_type': r.grantee_type,
                'grantee_id': r.grantee_id,
                'permission': r.permission,
                'granted_by': r.granted_by,
                'created_time': r.created_time,
            }
            for r in rows
        ]

    @staticmethod
    async def upsert_share(
        db: AsyncSession,
        *,
        resource_type: str,
        resource_id: str,
        owner_hasn_id: str,
        grantee_type: str,
        grantee_id: str,
        permission: str,
        granted_by: str,
        granter_permission: str | None = None,
    ) -> dict:
        """加 / 改一条授权：同 grantee 已有 active 行则改权限，否则新建。

        doc33 S2-5·doc32 §9 守卫 2 运行时半：建行前 fail-closed 校验 `resource_type` 已注册 G6 adapter
        （「能分享、必能判」在源头闭环——分享一个 G6 判不了的资源类型即拒，杜绝「建了 share 却判不出档位」）。
        S3 铺开完成后启用：全部可分享类型（knowledge/knowledge_doc/deck/designsystem/studio_project/
        studio_artifact/design）的 adapter 均由 app 启动 import 路由链（→ ai_native_app_registry）注册；
        测试经 tests/conftest.py 会话级注册，故任何运行模式下合法分享都不会误伤。

        doc32 §15.9 规则二·授予上限（S7）：`granter_permission` 传入时，本次授出档位不得高于授予人自身档位
        （`rank(permission) ≤ rank(granter_permission)`，防越权提权——如被分享的 editor 不得把别人设成 manager）。
        不传（None）= 沿旧行为不校验（owner 直授路径已由 owner=manager 自然满足，无需显式上限）。
        """
        # fail-closed：注册表无此 resource_type 的 adapter → 拒绝建行（能分享必能判·doc33 S2-5）。
        if resource_type not in resource_kind_registry.registered_types():
            raise errors.ServerError(
                msg=f'资源类型 {resource_type!r} 未注册 G6 权限适配器，拒绝建立分享（能分享必能判·doc33 S2-5）'
            )
        # 授予上限（doc32 §15.9 规则二）：不得授出高于自身的档位。
        if granter_permission is not None and rank(permission) > rank(granter_permission):
            raise errors.ForbiddenError(
                msg=f'不能授予高于自身的权限档位（自身 {granter_permission!r}，欲授 {permission!r}）'
            )
        existing = (
            await db.execute(
                select(HasnResourceShare).where(
                    HasnResourceShare.resource_type == resource_type,
                    HasnResourceShare.resource_id == resource_id,
                    HasnResourceShare.grantee_type == grantee_type,
                    HasnResourceShare.grantee_id == grantee_id,
                    HasnResourceShare.status == 'active',
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.permission = permission
            existing.granted_by = granted_by
            existing.updated_time = timezone.now()
            await db.flush()
            row = existing
        else:
            row = HasnResourceShare(
                resource_type=resource_type,
                resource_id=resource_id,
                owner_hasn_id=owner_hasn_id,
                grantee_type=grantee_type,
                grantee_id=grantee_id,
                permission=permission,
                granted_by=granted_by,
                status='active',
            )
            db.add(row)
            await db.flush()
        return {
            'id': row.id,
            'grantee_type': row.grantee_type,
            'grantee_id': row.grantee_id,
            'permission': row.permission,
        }

    @staticmethod
    async def revoke_share(
        db: AsyncSession, *, resource_type: str, resource_id: str, grantee_type: str, grantee_id: str
    ) -> bool:
        """撤销一条授权（status→revoked）。返回是否命中。"""
        existing = (
            await db.execute(
                select(HasnResourceShare).where(
                    HasnResourceShare.resource_type == resource_type,
                    HasnResourceShare.resource_id == resource_id,
                    HasnResourceShare.grantee_type == grantee_type,
                    HasnResourceShare.grantee_id == grantee_id,
                    HasnResourceShare.status == 'active',
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            return False
        existing.status = 'revoked'
        existing.updated_time = timezone.now()
        await db.flush()
        return True

    # ---------- 「共享给我的」反查（列表用） ----------

    @staticmethod
    async def shared_resource_ids_for_human(db: AsyncSession, *, resource_type: str, human_hasn_id: str) -> set[str]:
        """某主人能经「显式共享」看到的产物 id 集合（直接共享给人 + 经其企业共享）。

        不含 owner 自有 / 企业可见（visibility）部分——那些由 deck service 各自查。
        """
        memberships = await ResourceShareService.acting_human_memberships(db, human_hasn_id)
        member_enterprise_ids = {str(eid) for eid, _ in memberships}
        conds = [and_(HasnResourceShare.grantee_type == 'human', HasnResourceShare.grantee_id == human_hasn_id)]
        if member_enterprise_ids:
            conds.append(
                and_(
                    HasnResourceShare.grantee_type == 'enterprise',
                    HasnResourceShare.grantee_id.in_(member_enterprise_ids),
                )
            )
        rows = (
            (
                await db.execute(
                    select(HasnResourceShare.resource_id).where(
                        HasnResourceShare.resource_type == resource_type,
                        HasnResourceShare.status == 'active',
                        or_(*conds),
                    )
                )
            )
            .scalars()
            .all()
        )
        return set(rows)


resource_share_service = ResourceShareService()

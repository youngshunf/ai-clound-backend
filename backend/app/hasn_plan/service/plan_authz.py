"""规划应用·企业可见性 WHO 轴（数据范围档）+ 企业事件读授权（PLAN-ENT A3，[04] §4）。

沿 [02] 判权引擎不新造：
- 复用 ``ResourceShareService.acting_human_memberships``（hasn_id→企业成员关系[(eid, role)]）判企业身份/角色；
- 复用 ``hasn_enterprise_member_role``（部门桥，``kind='department'``）解析「本部门同事」集合；
- 复用 ``hasn_resource_share``（``resource_type='plan_event'``）判事件显式共享（含 role/enterprise/human 授予）。
判定恒前置 ``enterprise_id == E``（冻结不变量 #2）；分身无独立企业身份 → 按主人 hasn_id 解析（[02]）。

**WHO（数据范围档，[04] §4.2）**——决定「能看到谁的日程忙闲」：
- owner/admin → ``all``（企业全体成员）；
- 普通成员 → ``dept``（本部门同事 + 自己 + 被邀 + 被共享）；无部门则退化只见自己；
- ``self`` / ``dept_and_below`` 为设计枚举：首期无部门树，``dept_and_below`` 等同 ``dept``，``self`` 仅自己。
数据范围**外**的事件直接不返回（连「忙」都不露，[04] PE-D2）。**WHAT（露多细）**见 ``plan_visibility.py``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import sqlalchemy as sa

from backend.app.hasn.crud.crud_hasn_humans import hasn_humans_dao
from backend.app.hasn.model.hasn_enterprise_member_role import HasnEnterpriseMemberRole
from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn.model.hasn_enterprise_role import HasnEnterpriseRole
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.model.hasn_resource_share import HasnResourceShare
from backend.app.hasn.service.resource_share_service import ResourceShareService
from backend.app.hasn_plan.model import Event, EventAttendee

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

DataScope = Literal['self', 'dept', 'dept_and_below', 'all']

# plan 事件在 resource_share 里的 resource_type（A4 event.invite / 共享用同一口径）
PLAN_EVENT_RESOURCE_TYPE = 'plan_event'
_ADMIN_ROLES = frozenset({'owner', 'admin'})
_MEMBERSHIP_ACTIVE = ('approved',)


@dataclass(frozen=True)
class PlanEnterpriseScope:
    """viewer 在企业 E 的可见性事实（一次解析，供事件读授权复用）。"""

    viewer_owner_hasn_id: str
    enterprise_id: int
    is_member: bool  # viewer 是否 E 的 approved 成员（否 → 企业隔离，任何企业条目都不返回）
    data_scope: DataScope
    visible_member_hasn_ids: frozenset[str]  # WHO：可见忙闲的成员 hasn_id 集（恒含自己）
    attendee_event_ids: frozenset[int]  # 我被邀的事件 id（→ full）
    shared_event_ids: frozenset[int]  # 显式共享给我的事件 id（→ full，可跨数据范围）


async def _resolve_visible_members(
    db: AsyncSession,
    *,
    enterprise_id: int,
    viewer_user_id: int | None,
    viewer_owner_hasn_id: str,
    data_scope: DataScope,
) -> frozenset[str]:
    """据数据范围档解析「可见忙闲」的成员 hasn_id 集合（恒含自己）。"""
    visible: set[str] = {viewer_owner_hasn_id}
    if data_scope == 'self' or viewer_user_id is None:
        return frozenset(visible)

    if data_scope == 'all':
        rows = (
            (
                await db.execute(
                    sa
                    .select(HasnHumans.hasn_id)
                    .join(HasnEnterpriseMembership, HasnEnterpriseMembership.user_id == HasnHumans.user_id)
                    .where(
                        HasnEnterpriseMembership.enterprise_id == enterprise_id,
                        HasnEnterpriseMembership.status.in_(_MEMBERSHIP_ACTIVE),
                    )
                )
            )
            .scalars()
            .all()
        )
        visible.update(h for h in rows if h)
        return frozenset(visible)

    # data_scope in ('dept', 'dept_and_below')：本部门同事（首期无部门树，dept_and_below≡dept）
    dept_role_ids = (
        sa
        .select(HasnEnterpriseMemberRole.role_id)
        .join(HasnEnterpriseRole, HasnEnterpriseRole.id == HasnEnterpriseMemberRole.role_id)
        .where(
            HasnEnterpriseMemberRole.enterprise_id == enterprise_id,
            HasnEnterpriseMemberRole.user_id == viewer_user_id,
            HasnEnterpriseRole.kind == 'department',
        )
        .scalar_subquery()
    )
    peer_user_ids = (
        sa
        .select(sa.distinct(HasnEnterpriseMemberRole.user_id))
        .where(
            HasnEnterpriseMemberRole.enterprise_id == enterprise_id,
            HasnEnterpriseMemberRole.role_id.in_(dept_role_ids),
        )
        .scalar_subquery()
    )
    rows = (
        (await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id.in_(peer_user_ids)))).scalars().all()
    )
    visible.update(h for h in rows if h)
    return frozenset(visible)


async def _shared_plan_event_ids(
    db: AsyncSession,
    *,
    viewer_owner_hasn_id: str,
    enterprise_id: int,
    memberships: list[tuple[int, str]],
) -> frozenset[int]:
    """显式共享给 viewer 的 plan 事件 id 集（human / enterprise / role 三类授予，复用 [02] grantee 口径）。"""
    role_grantee_ids = await ResourceShareService._role_grantee_ids(
        db,
        subject_owner_hasn_id=viewer_owner_hasn_id,
        resource_enterprise_id=enterprise_id,
        memberships=memberships,
    )
    conds = [
        sa.and_(HasnResourceShare.grantee_type == 'human', HasnResourceShare.grantee_id == viewer_owner_hasn_id),
        sa.and_(HasnResourceShare.grantee_type == 'enterprise', HasnResourceShare.grantee_id == str(enterprise_id)),
    ]
    if role_grantee_ids:
        conds.append(
            sa.and_(HasnResourceShare.grantee_type == 'role', HasnResourceShare.grantee_id.in_(role_grantee_ids))
        )
    rows = (
        (
            await db.execute(
                sa.select(HasnResourceShare.resource_id).where(
                    HasnResourceShare.resource_type == PLAN_EVENT_RESOURCE_TYPE,
                    HasnResourceShare.status == 'active',
                    sa.or_(*conds),
                )
            )
        )
        .scalars()
        .all()
    )
    return frozenset(int(r) for r in rows if str(r).isdigit())


async def resolve_plan_enterprise_scope(
    db: AsyncSession, *, viewer_owner_hasn_id: str, enterprise_id: int
) -> PlanEnterpriseScope:
    """解析 viewer 在企业 E 的可见性事实（WHO + 被邀 + 被共享）。非成员 → is_member=False（企业隔离）。"""
    memberships = await ResourceShareService.acting_human_memberships(db, viewer_owner_hasn_id)
    role_in_ent = next((role for eid, role in memberships if eid == enterprise_id), None)
    if role_in_ent is None:
        return PlanEnterpriseScope(
            viewer_owner_hasn_id=viewer_owner_hasn_id,
            enterprise_id=enterprise_id,
            is_member=False,
            data_scope='self',
            visible_member_hasn_ids=frozenset(),
            attendee_event_ids=frozenset(),
            shared_event_ids=frozenset(),
        )

    human = await hasn_humans_dao.get_by_hasn_id(db, viewer_owner_hasn_id)
    viewer_user_id = human.user_id if human is not None else None
    data_scope: DataScope = 'all' if role_in_ent in _ADMIN_ROLES else 'dept'

    visible = await _resolve_visible_members(
        db,
        enterprise_id=enterprise_id,
        viewer_user_id=viewer_user_id,
        viewer_owner_hasn_id=viewer_owner_hasn_id,
        data_scope=data_scope,
    )
    attendee_rows = (
        (
            await db.execute(
                sa.select(EventAttendee.event_id).where(
                    EventAttendee.enterprise_id == enterprise_id,
                    EventAttendee.attendee_hasn_id == viewer_owner_hasn_id,
                )
            )
        )
        .scalars()
        .all()
    )
    shared = await _shared_plan_event_ids(
        db, viewer_owner_hasn_id=viewer_owner_hasn_id, enterprise_id=enterprise_id, memberships=memberships
    )
    return PlanEnterpriseScope(
        viewer_owner_hasn_id=viewer_owner_hasn_id,
        enterprise_id=enterprise_id,
        is_member=True,
        data_scope=data_scope,
        visible_member_hasn_ids=visible,
        attendee_event_ids=frozenset(int(e) for e in attendee_rows),
        shared_event_ids=shared,
    )


def enterprise_event_who_filter(scope: PlanEnterpriseScope) -> sa.ColumnElement[bool]:
    """WHO 轴 SQL 过滤：可见成员的事件 ∪ 企业公开 ∪ 我被邀 ∪ 我被共享（恒前置 enterprise_id==E 由调用方加）。"""
    clauses: list[sa.ColumnElement[bool]] = [Event.visibility == 'public']
    if scope.visible_member_hasn_ids:
        clauses.append(Event.owner_hasn_id.in_(scope.visible_member_hasn_ids))
    if scope.attendee_event_ids:
        clauses.append(Event.id.in_(scope.attendee_event_ids))
    if scope.shared_event_ids:
        clauses.append(Event.id.in_(scope.shared_event_ids))
    return sa.or_(*clauses)

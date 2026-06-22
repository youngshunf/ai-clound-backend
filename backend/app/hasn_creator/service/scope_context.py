"""创作企业化查询上下文与角色裁剪（设计 00 §11 / v3 §6.7；仿 hasn_growth scope_context）。

把「当前请求以谁的身份、看哪批数据」收敛为一个 `CreatorScope`：
- personal（active_enterprise_id 为空）→ holder=user_id，看自己全部；
- enterprise + 主编（企业 owner/admin）→ 看本企业全部，view=mine 时仅看自己 assignee 的；
- enterprise + 运营（普通 member）→ 恒只看 assignee=自己 的项目（及其子对象）。

主编 vs 运营映射：读平台 `hasn_enterprise_membership` 内置 role —— owner/admin=主编(manager)、
member=运营(member)。自定义角色/部门（hasn_enterprise_role）属正交维度，不参与主编/运营判定。

裁剪真相在后端：`apply_scope` 给查询加 owner_scope/enterprise_id/assignee 条件；前端的 view 只是请求意图，
越权由后端拒（member 传 view=team 也只回自己的）。业务系统型应用不走 resource_share（§6.7）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn_core import HasnHumans
from backend.app.home.model.hasn_owner_workbench_pref import HasnOwnerWorkbenchPref

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 企业 owner/admin 视为「主编」（看全部、可分配）；member 为「运营」（只看自己 assignee）。
_MANAGER_ROLES = ('owner', 'admin')
_MEMBERSHIP_ACTIVE = ('approved',)


@dataclass(frozen=True)
class CreatorScope:
    """创作请求归属上下文。personal 模式 enterprise_id 为 None、viewer_role='owner'。"""

    user_id: int
    owner_hasn_id: str | None = None  # 当前行动主人的 hasn_id（enterprise 模式作 assignee 键）
    enterprise_id: int | None = None  # None = 个人上下文
    viewer_role: str = 'owner'  # owner（个人）| manager（主编）| member（运营）
    view: str = 'team'  # team | mine —— 企业内请求意图（主编可切「我的」）

    @property
    def is_enterprise(self) -> bool:
        return self.enterprise_id is not None

    @property
    def is_manager(self) -> bool:
        return self.viewer_role == 'manager'

    @property
    def restrict_to_self(self) -> bool:
        """是否仅看自己 assignee 的：运营恒 True；主编仅当 view=mine 时 True。"""
        if not self.is_enterprise:
            return False
        return self.viewer_role == 'member' or self.view == 'mine'

    def to_meta(self) -> dict:
        """回传给前端的上下文元信息（驱动 UI 显隐；权限真相仍在后端）。"""
        return {
            'owner_scope': 'enterprise' if self.is_enterprise else 'personal',
            'enterprise_id': self.enterprise_id,
            'viewer_role': self.viewer_role,
            'view': self.view if self.is_enterprise else None,
        }


async def _resolve_owner_hasn_id(db: AsyncSession, user_id: int) -> str | None:
    return (await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id))).scalars().first()


async def _active_enterprise_id(db: AsyncSession, owner_hasn_id: str) -> int | None:
    eid = (
        (
            await db.execute(
                sa.select(HasnOwnerWorkbenchPref.active_enterprise_id).where(
                    HasnOwnerWorkbenchPref.owner_hasn_id == owner_hasn_id
                )
            )
        )
        .scalars()
        .first()
    )
    return int(eid) if eid else None


async def _membership_role(db: AsyncSession, *, user_id: int, enterprise_id: int) -> str | None:
    """主人在该企业的 approved 成员 role（owner/admin/member）；非成员返回 None。"""
    return (
        (
            await db.execute(
                sa.select(HasnEnterpriseMembership.role).where(
                    HasnEnterpriseMembership.enterprise_id == enterprise_id,
                    HasnEnterpriseMembership.user_id == user_id,
                    HasnEnterpriseMembership.status.in_(_MEMBERSHIP_ACTIVE),
                )
            )
        )
        .scalars()
        .first()
    )


async def resolve_creator_scope(
    db: AsyncSession,
    *,
    user_id: int,
    owner_hasn_id: str | None = None,
    view: str = 'team',
) -> CreatorScope:
    """从请求解析创作上下文（owner/agent 端点共用）。

    owner_hasn_id 缺省时按 user_id 解析（owner JWT）；agent 端点直接传 token 里的 owner_hasn_id。
    active_enterprise_id 为空 → personal。失 approved 成员资格 → 自愈回落 personal。
    """
    view = 'mine' if view == 'mine' else 'team'
    if owner_hasn_id is None:
        owner_hasn_id = await _resolve_owner_hasn_id(db, user_id)
    if not owner_hasn_id:
        return CreatorScope(user_id=user_id, owner_hasn_id=None)

    enterprise_id = await _active_enterprise_id(db, owner_hasn_id)
    if enterprise_id is None:
        return CreatorScope(user_id=user_id, owner_hasn_id=owner_hasn_id)

    role = await _membership_role(db, user_id=user_id, enterprise_id=enterprise_id)
    if role is None:
        # 已不是该企业 approved 成员 → 自愈回落个人上下文（不在读路径写库）。
        return CreatorScope(user_id=user_id, owner_hasn_id=owner_hasn_id)

    viewer_role = 'manager' if role in _MANAGER_ROLES else 'member'
    return CreatorScope(
        user_id=user_id,
        owner_hasn_id=owner_hasn_id,
        enterprise_id=enterprise_id,
        viewer_role=viewer_role,
        view=view,
    )


def apply_scope(stmt, model, *, user_id: int, scope: CreatorScope | None):
    """给 select 语句加归属裁剪条件（model 须有 owner_scope/enterprise_id/assignee/user_id 列）。

    - scope=None（内部 worker / 旧测试路径）→ 仅 user_id（不限 owner_scope）：完全向后兼容。
    - scope=personal（端点显式个人上下文）→ owner_scope='personal' AND user_id（个人模式不见企业行）。
    - scope=enterprise 主编（view=team）→ owner_scope='enterprise' AND enterprise_id=X（全企业）。
    - scope=enterprise 运营 或 主编 view=mine → 再加 assignee=自己。
    """
    if scope is None:
        return stmt.where(model.user_id == user_id)
    if not scope.is_enterprise:
        return stmt.where(model.owner_scope == 'personal', model.user_id == user_id)
    conds = [model.owner_scope == 'enterprise', model.enterprise_id == scope.enterprise_id]
    if scope.restrict_to_self:
        conds.append(model.assignee == scope.owner_hasn_id)
    return stmt.where(*conds)


def ownership_fields(scope: CreatorScope | None, *, user_id: int) -> dict:
    """建对象时落归属列（owner_scope/user_id/enterprise_id/assignee）。

    - 无 scope / personal → owner_scope='personal'、enterprise_id=None、assignee=owner_hasn_id（若有）。
    - enterprise → owner_scope='enterprise'、enterprise_id=X、assignee=当前主人 hasn_id
      （运营/主编建的都归自己，可后续 reassign）。
    """
    if scope is None or not scope.is_enterprise:
        return {
            'owner_scope': 'personal',
            'user_id': user_id,
            'enterprise_id': None,
            'assignee': scope.owner_hasn_id if scope else None,
        }
    return {
        'owner_scope': 'enterprise',
        'user_id': user_id,
        'enterprise_id': scope.enterprise_id,
        'assignee': scope.owner_hasn_id,
    }


def can_manage_assignment(scope: CreatorScope | None) -> bool:
    """是否有分配/转移负责人权限：仅企业主编（owner/admin）。个人/运营不可分配。"""
    return scope is not None and scope.is_enterprise and scope.is_manager


async def validate_enterprise_member_hasn_id(
    db: AsyncSession, *, enterprise_id: int, owner_hasn_id: str
) -> bool:
    """校验 owner_hasn_id 是该企业的 approved 成员（reassign 目标合法性）。

    assignee 键是主人 hasn_id；成员表 `hasn_enterprise_membership` 按 user_id，故 join `HasnHumans`
    把 hasn_id→user_id 再查成员资格。非成员/不存在 → False（拒绝把项目转给企业外的人）。
    """
    row = (
        await db.execute(
            sa.select(HasnEnterpriseMembership.id)
            .join(HasnHumans, HasnHumans.user_id == HasnEnterpriseMembership.user_id)
            .where(
                HasnEnterpriseMembership.enterprise_id == enterprise_id,
                HasnHumans.hasn_id == owner_hasn_id,
                HasnEnterpriseMembership.status.in_(_MEMBERSHIP_ACTIVE),
            )
        )
    ).first()
    return row is not None

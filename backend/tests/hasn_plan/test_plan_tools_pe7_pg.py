"""PLAN-ENT A4：plan 平台工具 PE-7 空间入参 + 企业协同（invite/rsvp/availability）真实 PG 验证。

零 mock：真实本地 PostgreSQL(15432) 直调 backend.app.mcp.tools.plan 的 handler（分身工具体），
handler → plan_service（in-process 权威 service）。事务回滚不污染库。需要：export DATABASE_PORT=15432。

覆盖（施工清单 A4 pytest 项，[04] §6.1/§6.2/§6.3）：
- PE-7 空间三分支：personal 始终允许 / enterprise 自动填企业归属 / enterprise 但不在企业空间诚实拒绝；
- 快照优先：`_active_enterprise_id` 快照命中（异步派发路径），无活跃空间也能落企业（且仍校验成员）；
- event.create 企业事件自动展开组织者行 + 受邀参会行；
- invite 加/减参会人（组织者不可移除）+ rsvp 回复往返；
- availability 只回忙闲块不回标题 + 受 A3 可见性约束（跨部门/非成员排除）；
- owner_decision actor 放行（A2 分诊四态，无 notes 不误拦）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import sqlalchemy as sa

from backend.app.hasn.model.hasn_enterprise_member_role import HasnEnterpriseMemberRole
from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn.model.hasn_enterprise_role import HasnEnterpriseRole
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_plan.model import Todo
from backend.app.hasn_plan.service.plan_app_service import plan_service
from backend.app.home.model.hasn_owner_workbench_pref import HasnOwnerWorkbenchPref
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.tools.plan import (
    ERR_NOT_IN_ENTERPRISE_SPACE,
    _h_availability,
    _h_create_event,
    _h_create_todo,
    _h_event_invite,
    _h_event_rsvp,
)
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')

_T0 = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 7, 2, 10, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _World:
    enterprise_id: int
    dept_sales_id: int
    viewer: str  # 销售部普通成员，pref.active_enterprise_id=E（活跃企业空间）
    viewer_uid: int
    dept_peer: str  # 同部门（销售部）同事，**无** pref（用于快照优先分支）
    dept_peer_uid: int
    cross_peer: str  # 工程部同事（跨部门，超 viewer 数据范围）
    cross_peer_uid: int
    loner: str  # 无任何企业成员关系（用于 enterprise-in-personal 诚实拒绝分支）
    loner_uid: int


def _mk_ctx(owner_hasn_id: str, owner_uid: int, *, agent_name: str = '规划分身') -> AgentContext:
    """构造一个 agent 执行上下文（身份取自「凭证」，绝不入 body）。"""
    return AgentContext(
        hasn_id=f'a_{uuid4().hex[:16]}',
        owner_id=owner_uid,
        agent_status='active',
        metadata={},
        agent_name=agent_name,
        owner_hasn_id=owner_hasn_id,
    )


async def _seed_world(db) -> _World:  # noqa: ANN001
    """播种：企业 E + 销售/工程两部门 + 3 成员（viewer 有活跃空间 pref）+ 1 无企业身份 loner。"""
    base = 710_000_000 + (uuid4().int % 100_000_000)
    e_id = base + 10
    uv, up, uc, ul = base, base + 1, base + 2, base + 3
    hv = f'h_{uuid4().hex[:16]}'
    hp = f'h_{uuid4().hex[:16]}'
    hc = f'h_{uuid4().hex[:16]}'
    hl = f'h_{uuid4().hex[:16]}'

    for hasn_id, user_id in ((hv, uv), (hp, up), (hc, uc), (hl, ul)):
        db.add(HasnHumans(hasn_id=hasn_id, user_id=user_id, star_id=str(user_id), nickname=hasn_id, status='active'))

    # viewer/dept_peer/cross_peer 属 E（approved）；loner 无任何成员关系
    for user_id in (uv, up, uc):
        db.add(HasnEnterpriseMembership(enterprise_id=e_id, user_id=user_id, role='member', status='approved'))

    dept_sales = HasnEnterpriseRole(enterprise_id=e_id, name='销售部', kind='department')
    dept_eng = HasnEnterpriseRole(enterprise_id=e_id, name='工程部', kind='department')
    db.add_all([dept_sales, dept_eng])
    await db.flush()

    db.add_all([
        HasnEnterpriseMemberRole(enterprise_id=e_id, user_id=uv, role_id=dept_sales.id),
        HasnEnterpriseMemberRole(enterprise_id=e_id, user_id=up, role_id=dept_sales.id),
        HasnEnterpriseMemberRole(enterprise_id=e_id, user_id=uc, role_id=dept_eng.id),
    ])
    # 仅 viewer 有活跃企业空间偏好（dept_peer 故意不设，用于快照优先分支）
    db.add(HasnOwnerWorkbenchPref(owner_hasn_id=hv, active_enterprise_id=e_id))
    await db.flush()

    return _World(
        enterprise_id=e_id,
        dept_sales_id=dept_sales.id,
        viewer=hv,
        viewer_uid=uv,
        dept_peer=hp,
        dept_peer_uid=up,
        cross_peer=hc,
        cross_peer_uid=uc,
        loner=hl,
        loner_uid=ul,
    )


async def test_capture_personal_default_always_allowed() -> None:
    """PE-7：省略 scope（默认 personal）→ 建个人待办（enterprise_id 为 None），企业成员也不例外。"""
    async with async_db_session() as db:
        try:
            w = await _seed_world(db)
            ctx = _mk_ctx(w.viewer, w.viewer_uid)
            row = await _h_create_todo(db, ctx, {'title': '个人待办：读一本书'})
            assert row.get('enterprise_id') is None
            assert row.get('owner_hasn_id') == w.viewer
        finally:
            await db.rollback()


async def test_capture_enterprise_autofills_from_active_space() -> None:
    """PE-7：scope=enterprise 且在活跃企业空间 → 自动填 enterprise_id + owner 所在部门 dept_id。"""
    async with async_db_session() as db:
        try:
            w = await _seed_world(db)
            ctx = _mk_ctx(w.viewer, w.viewer_uid)
            row = await _h_create_todo(db, ctx, {'title': '企业待办：写周报', 'scope': 'enterprise'})
            assert row.get('enterprise_id') == w.enterprise_id
            assert row.get('dept_id') == w.dept_sales_id  # viewer 在销售部
        finally:
            await db.rollback()


async def test_enterprise_scope_in_personal_space_rejects() -> None:
    """PE-7：scope=enterprise 但调用方不在企业空间（无成员/无活跃空间）→ 诚实拒绝，绝不落库、不切换。"""
    async with async_db_session() as db:
        try:
            w = await _seed_world(db)
            ctx = _mk_ctx(w.loner, w.loner_uid)
            row = await _h_create_todo(db, ctx, {'title': '越权企业待办', 'scope': 'enterprise'})
            assert row.get('ok') is False
            assert row.get('error_code') == ERR_NOT_IN_ENTERPRISE_SPACE
            # 一条都不落库
            cnt = (
                await db.execute(sa.select(sa.func.count()).select_from(Todo).where(Todo.owner_hasn_id == w.loner))
            ).scalar_one()
            assert cnt == 0
        finally:
            await db.rollback()


async def test_snapshot_enterprise_takes_priority_over_active_space() -> None:
    """PE-7：异步派发路径——传 `_active_enterprise_id` 快照，无活跃空间 pref 也能落企业（仍校验成员）。"""
    async with async_db_session() as db:
        try:
            w = await _seed_world(db)
            ctx = _mk_ctx(w.dept_peer, w.dept_peer_uid)  # dept_peer 无 pref
            # 无快照且 scope=enterprise → 应诚实拒绝（无活跃空间）
            rejected = await _h_create_todo(db, ctx, {'title': '无快照企业待办', 'scope': 'enterprise'})
            assert rejected.get('error_code') == ERR_NOT_IN_ENTERPRISE_SPACE
            # 带快照 → 命中企业（dept_peer 是 E 的 approved 成员，通过 fail-safe 校验）
            row = await _h_create_todo(
                db, ctx, {'title': '快照企业待办', 'scope': 'enterprise', '_active_enterprise_id': w.enterprise_id}
            )
            assert row.get('enterprise_id') == w.enterprise_id
            assert row.get('dept_id') == w.dept_sales_id  # dept_peer 也在销售部
        finally:
            await db.rollback()


async def test_owner_decision_actor_passthrough() -> None:
    """A2 分诊四态：actor=owner_decision（待你决策·可派发）建待办应放行——非 agent/collab，无 notes 也不拦。"""
    async with async_db_session() as db:
        try:
            w = await _seed_world(db)
            ctx = _mk_ctx(w.viewer, w.viewer_uid)
            row = await _h_create_todo(db, ctx, {'title': '需你拍板：是否签这单', 'actor': 'owner_decision'})
            assert 'id' in row  # 未被 notes_required 拦截
            assert row.get('actor') == 'owner_decision'
        finally:
            await db.rollback()


async def test_create_enterprise_event_seeds_organizer_and_attendees() -> None:
    """[04] §6.3：企业事件建成 → 自动展开组织者行（accepted）+ 受邀参会行（none）。"""
    async with async_db_session() as db:
        try:
            w = await _seed_world(db)
            ctx = _mk_ctx(w.viewer, w.viewer_uid)
            ev = await _h_create_event(
                db,
                ctx,
                {
                    'title': '季度评审会',
                    'start_at': _T0,
                    'end_at': _T1,
                    'scope': 'enterprise',
                    'attendees': [w.dept_peer, w.cross_peer],
                },
            )
            assert ev.get('enterprise_id') == w.enterprise_id
            attendees = await plan_service.list_attendees(db, event_id=int(ev['id']))
            by_h = {a['attendee_hasn_id']: a for a in attendees}
            assert by_h[w.viewer]['role'] == 'organizer'
            assert by_h[w.viewer]['rsvp'] == 'accepted'
            assert by_h[w.dept_peer]['role'] == 'required'
            assert by_h[w.dept_peer]['rsvp'] == 'none'
            assert by_h[w.cross_peer]['role'] == 'required'  # 参会可跨部门
        finally:
            await db.rollback()


async def test_invite_and_rsvp_roundtrip_organizer_immovable() -> None:
    """[04] §6.2：invite 加人 → rsvp 回复 → 组织者不可被移除。"""
    async with async_db_session() as db:
        try:
            w = await _seed_world(db)
            organizer_ctx = _mk_ctx(w.viewer, w.viewer_uid)
            ev = await _h_create_event(
                db, organizer_ctx, {'title': '方案对齐', 'start_at': _T0, 'end_at': _T1, 'scope': 'enterprise'}
            )
            eid = int(ev['id'])
            # 加参会人
            inv = await _h_event_invite(db, organizer_ctx, {'event_id': eid, 'add': [w.dept_peer]})
            assert w.dept_peer in inv['added']
            # 被邀人回复 RSVP
            peer_ctx = _mk_ctx(w.dept_peer, w.dept_peer_uid)
            rsvp = await _h_event_rsvp(db, peer_ctx, {'event_id': eid, 'rsvp': 'accepted'})
            assert rsvp['rsvp'] == 'accepted'
            assert rsvp['attendee_hasn_id'] == w.dept_peer
            # 组织者本人不可被移除
            rm = await _h_event_invite(db, organizer_ctx, {'event_id': eid, 'remove': [w.viewer, w.dept_peer]})
            assert w.viewer not in rm['removed']
            assert w.dept_peer in rm['removed']
            still = await plan_service.list_attendees(db, event_id=eid)
            assert any(a['attendee_hasn_id'] == w.viewer and a['role'] == 'organizer' for a in still)
        finally:
            await db.rollback()


async def test_availability_busy_only_and_scope_constrained() -> None:
    """[04] §6.2：查忙闲——只回匿名忙碌块（不泄标题），且受 A3 数据范围约束（跨部门/非成员排除）。"""
    async with async_db_session() as db:
        try:
            w = await _seed_world(db)
            # dept_peer（同部门）与 cross_peer（跨部门）各建一个企业私密事件
            for owner, dept in ((w.dept_peer, w.dept_sales_id), (w.cross_peer, None)):
                await plan_service.create_event(
                    db,
                    owner=owner,
                    data={'title': f'{owner} 的私密会', 'start_at': _T0, 'end_at': _T1, 'visibility': 'private'},
                    enterprise_id=w.enterprise_id,
                    dept_id=dept,
                )
            viewer_ctx = _mk_ctx(w.viewer, w.viewer_uid)
            avail = await _h_availability(
                db,
                viewer_ctx,
                {'enterprise_id': w.enterprise_id, 'members': [w.dept_peer, w.cross_peer, w.loner]},
            )
            # 同部门同事：可见，但只回忙闲块（标题裁成「忙碌」、不泄原标题）
            assert w.dept_peer in avail and len(avail[w.dept_peer]) == 1
            block = avail[w.dept_peer][0]
            assert block['title'] == '忙碌'
            assert block.get('busy') is True
            assert block.get('start_at') and block.get('end_at')
            # 跨部门（超数据范围）与非成员：排除，连忙闲都不返回
            assert w.cross_peer not in avail
            assert w.loner not in avail
        finally:
            await db.rollback()

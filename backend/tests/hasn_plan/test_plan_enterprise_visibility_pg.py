"""PLAN-ENT A3：企业事件可见性两轴（WHO 数据范围档 + WHAT 忙闲裁剪）真实 PG 验证。

零 mock：真实本地 PostgreSQL(15432) 跑 PlanService.list_enterprise_events + plan_authz 判权引擎，
复用 [02] 的企业成员/部门桥/resource_share（不新造判权）。事务回滚不污染库。
需要：export DATABASE_PORT=15432。

覆盖（施工清单 A3 pytest 项，[04] §4.2）：
- 同部门见忙闲不见标题（数据范围内同事私有事件 → busy 裁剪，隐藏标题）；
- 被邀参会人见全详情（event_attendee → full，会议对参会人天然透明，可跨部门）；
- 跨部门（超数据范围）全隐（既非可见成员、又非被邀/被共享/公开 → 连忙闲都不返回，PE-D2）；
- resource_share / public 放开详情（显式共享 or 企业公开 → full，可跨数据范围）；
- 企业隔离（非本企业成员读企业 E → 一条不返回，冻结不变量 #2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from backend.app.hasn.model.hasn_enterprise_member_role import HasnEnterpriseMemberRole
from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn.model.hasn_enterprise_role import HasnEnterpriseRole
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.model.hasn_resource_share import HasnResourceShare
from backend.app.hasn_plan.model import EventAttendee
from backend.app.hasn_plan.service.plan_app_service import PlanService
from backend.app.hasn_plan.service.plan_authz import resolve_plan_enterprise_scope
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')

_T0 = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
_T1 = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _World:
    """一套企业可见性测试世界的标识（viewer 是普通成员=dept 档）。"""

    enterprise_id: int
    other_enterprise_id: int
    viewer: str  # 普通成员（销售部），data_scope='dept'
    dept_peer: str  # 同部门（销售部）同事
    cross_peer: str  # 跨部门（工程部）同事
    outsider: str  # 仅属另一企业 F，绝非 E 的成员
    ev_own: int  # viewer 自己的私密事件 → full
    ev_peer_private: int  # 同部门同事私密事件 → busy
    ev_invited: int  # 跨部门事件，viewer 被邀参会 → full
    ev_cross_hidden: int  # 跨部门私密事件，viewer 无任何关系 → 隐藏
    ev_public: int  # 跨部门企业公开事件 → full
    ev_shared: int  # 跨部门事件，显式 resource_share 给 viewer → full


async def _seed_world(db) -> _World:  # noqa: ANN001 (AsyncSession，避免测试头顶重复 import 噪声)
    """播种一套企业世界：4 成员 + 2 部门 + 6 事件 + 1 参会 + 1 共享。返回标识。"""
    base = 700_000_000 + (uuid4().int % 100_000_000)
    e_id, f_id = base + 10, base + 11
    uv, up, uc, uo = base, base + 1, base + 2, base + 3
    hv = f'h_{uuid4().hex[:16]}'
    hp = f'h_{uuid4().hex[:16]}'
    hc = f'h_{uuid4().hex[:16]}'
    ho = f'h_{uuid4().hex[:16]}'

    # 4 个 human（hasn_id ↔ user_id）
    for hasn_id, user_id in ((hv, uv), (hp, up), (hc, uc), (ho, uo)):
        db.add(HasnHumans(hasn_id=hasn_id, user_id=user_id, star_id=str(user_id), nickname=hasn_id, status='active'))

    # 企业成员关系：viewer/dept_peer/cross_peer 属 E（member 档），outsider 属 F（隔离用）
    for user_id, ent in ((uv, e_id), (up, e_id), (uc, e_id), (uo, f_id)):
        db.add(HasnEnterpriseMembership(enterprise_id=ent, user_id=user_id, role='member', status='approved'))

    # 两个部门（HasnEnterpriseRole kind='department'）
    dept_sales = HasnEnterpriseRole(enterprise_id=e_id, name='销售部', kind='department')
    dept_eng = HasnEnterpriseRole(enterprise_id=e_id, name='工程部', kind='department')
    db.add_all([dept_sales, dept_eng])
    await db.flush()

    # 成员↔部门：viewer+dept_peer→销售部，cross_peer→工程部
    db.add_all([
        HasnEnterpriseMemberRole(enterprise_id=e_id, user_id=uv, role_id=dept_sales.id),
        HasnEnterpriseMemberRole(enterprise_id=e_id, user_id=up, role_id=dept_sales.id),
        HasnEnterpriseMemberRole(enterprise_id=e_id, user_id=uc, role_id=dept_eng.id),
    ])
    await db.flush()

    svc = PlanService()

    async def _ev(owner: str, title: str, dept_id: int, visibility: str) -> int:
        row = await svc.create_event(
            db,
            owner=owner,
            data={'title': title, 'start_at': _T0, 'end_at': _T1, 'visibility': visibility},
            enterprise_id=e_id,
            dept_id=dept_id,
        )
        return int(row['id'])

    ev_own = await _ev(hv, '我自己的私密日程', dept_sales.id, 'private')
    ev_peer_private = await _ev(hp, '同部门同事的私密会', dept_sales.id, 'private')
    ev_invited = await _ev(hc, '邀请你参加的跨部门评审', dept_eng.id, 'private')
    ev_cross_hidden = await _ev(hc, '跨部门内部会（与你无关）', dept_eng.id, 'private')
    ev_public = await _ev(hc, '全员公开周会', dept_eng.id, 'public')
    ev_shared = await _ev(hc, '共享给你的跨部门方案', dept_eng.id, 'private')

    # viewer 被邀参会 ev_invited
    db.add(EventAttendee(event_id=ev_invited, enterprise_id=e_id, attendee_hasn_id=hv, role='required', rsvp='pending'))
    # ev_shared 显式 resource_share 给 viewer（human 授予）
    db.add(
        HasnResourceShare(
            resource_type='plan_event',
            resource_id=str(ev_shared),
            owner_hasn_id=hc,
            grantee_type='human',
            grantee_id=hv,
            permission='viewer',
            granted_by=hc,
            status='active',
        )
    )
    await db.flush()

    return _World(
        enterprise_id=e_id,
        other_enterprise_id=f_id,
        viewer=hv,
        dept_peer=hp,
        cross_peer=hc,
        outsider=ho,
        ev_own=ev_own,
        ev_peer_private=ev_peer_private,
        ev_invited=ev_invited,
        ev_cross_hidden=ev_cross_hidden,
        ev_public=ev_public,
        ev_shared=ev_shared,
    )


async def test_scope_is_dept_for_normal_member() -> None:
    """普通成员解析为 dept 档，可见成员集恰为「本部门同事 + 自己」（不含跨部门同事）。"""
    svc = PlanService()  # noqa: F841 (仅播种用；scope 解析走 plan_authz)
    async with async_db_session() as db:
        try:
            w = await _seed_world(db)
            scope = await resolve_plan_enterprise_scope(
                db, viewer_owner_hasn_id=w.viewer, enterprise_id=w.enterprise_id
            )
            assert scope.is_member is True
            assert scope.data_scope == 'dept'
            # 本部门：viewer(自己) + dept_peer；跨部门 cross_peer 不在可见成员集
            assert w.viewer in scope.visible_member_hasn_ids
            assert w.dept_peer in scope.visible_member_hasn_ids
            assert w.cross_peer not in scope.visible_member_hasn_ids
            # 被邀 / 被共享事实已解析
            assert w.ev_invited in scope.attendee_event_ids
            assert w.ev_shared in scope.shared_event_ids
        finally:
            await db.rollback()


async def test_same_dept_sees_busy_not_title() -> None:
    """同部门同事的私密事件 → 仅忙闲块（标题隐藏为「忙碌」、原标题/推理字段不泄漏）。"""
    async with async_db_session() as db:
        try:
            w = await _seed_world(db)
            svc = PlanService()
            events = await svc.list_enterprise_events(db, viewer_owner_hasn_id=w.viewer, enterprise_id=w.enterprise_id)
            by_id = {e['id']: e for e in events}
            peer = by_id[w.ev_peer_private]
            assert peer['title'] == '忙碌'
            assert peer.get('busy') is True
            assert peer.get('redacted') is True
            assert peer['title'] != '同部门同事的私密会'  # 原标题被裁剪
            # 时间块占用字段仍在（供忙闲格渲染）
            assert peer.get('start_at') and peer.get('end_at')
        finally:
            await db.rollback()


async def test_invited_attendee_sees_full() -> None:
    """被邀参会人见全详情（会议对参会人天然透明，可跨部门）。"""
    async with async_db_session() as db:
        try:
            w = await _seed_world(db)
            svc = PlanService()
            events = await svc.list_enterprise_events(db, viewer_owner_hasn_id=w.viewer, enterprise_id=w.enterprise_id)
            by_id = {e['id']: e for e in events}
            invited = by_id[w.ev_invited]
            assert invited['title'] == '邀请你参加的跨部门评审'  # 全详情
            assert 'redacted' not in invited
            # 自己的事件同样是全详情
            assert by_id[w.ev_own]['title'] == '我自己的私密日程'
            assert 'redacted' not in by_id[w.ev_own]
        finally:
            await db.rollback()


async def test_cross_dept_beyond_scope_hidden() -> None:
    """跨部门（超数据范围）私密事件、且非被邀/被共享 → 一条都不返回（连忙闲都不露，PE-D2）。"""
    async with async_db_session() as db:
        try:
            w = await _seed_world(db)
            svc = PlanService()
            events = await svc.list_enterprise_events(db, viewer_owner_hasn_id=w.viewer, enterprise_id=w.enterprise_id)
            ids = {e['id'] for e in events}
            assert w.ev_cross_hidden not in ids  # 彻底不返回
        finally:
            await db.rollback()


async def test_shared_or_public_opens_detail() -> None:
    """resource_share 显式共享 or event.visibility=public → 放开全详情（可跨数据范围）。"""
    async with async_db_session() as db:
        try:
            w = await _seed_world(db)
            svc = PlanService()
            events = await svc.list_enterprise_events(db, viewer_owner_hasn_id=w.viewer, enterprise_id=w.enterprise_id)
            by_id = {e['id']: e for e in events}
            # 企业公开事件 → 全详情
            assert by_id[w.ev_public]['title'] == '全员公开周会'
            assert 'redacted' not in by_id[w.ev_public]
            # 显式共享给我的跨部门事件 → 全详情
            assert by_id[w.ev_shared]['title'] == '共享给你的跨部门方案'
            assert 'redacted' not in by_id[w.ev_shared]
        finally:
            await db.rollback()


async def test_enterprise_isolation_non_member_gets_empty() -> None:
    """企业隔离：非本企业成员（属另一企业 F）读企业 E → 一条都不返回（冻结不变量 #2）。"""
    async with async_db_session() as db:
        try:
            w = await _seed_world(db)
            svc = PlanService()
            leaked = await svc.list_enterprise_events(
                db, viewer_owner_hasn_id=w.outsider, enterprise_id=w.enterprise_id
            )
            assert leaked == []
            scope = await resolve_plan_enterprise_scope(
                db, viewer_owner_hasn_id=w.outsider, enterprise_id=w.enterprise_id
            )
            assert scope.is_member is False
        finally:
            await db.rollback()

"""PLAN-ENT B2：今日双模空间分叉（个人组 ∪ 企业组，两条独立 scope-read）真实 PG 验证。

零 mock：真实本地 PostgreSQL(15432) 跑 PlanService.today_overview / list_enterprise_todos + plan_authz。
覆盖冻结不变量 #1（个人零破坏：个人组恒 enterprise_id IS NULL）、#2（企业「今日」= 两条独立读的并集，
不是一条混合查询）：
- 无活跃企业 → `enterprise=None`（纯个人，个人组照旧）；
- 有活跃企业 E → `enterprise` 组带 `enterprise_id=E` + 企业事件/待办（WHO/WHAT 裁剪）；
- 个人组绝不混入企业条目（owner 自己的企业待办/事件不出现在个人 inbox/events，防双计数）；
- `list_enterprise_todos` 数据范围过滤 + 非成员空。
需要：export DATABASE_PORT=15432。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_plan.service.plan_app_service import PlanService
from backend.app.home.model.hasn_owner_workbench_pref import HasnOwnerWorkbenchPref
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')

_DAY_START = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
_DAY_END = datetime(2026, 7, 1, 23, 59, tzinfo=timezone.utc)
_EV_START = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
_EV_END = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _World:
    enterprise_id: int
    viewer: str
    personal_todo_id: int
    ent_todo_id: int
    personal_event_id: int
    ent_event_id: int


async def _seed(db, *, active: bool) -> _World:  # noqa: ANN001
    """播种：viewer(企业 E 成员) + 个人待办/事件(enterprise_id NULL) + 企业待办/事件(=E)。

    `active=True` → 写 workbench pref active_enterprise_id=E（企业空间活跃）；False → 不写（纯个人）。
    """
    base = 720_000_000 + (uuid4().int % 100_000_000)
    e_id = base + 10
    uv = base
    hv = f'h_{uuid4().hex[:16]}'
    db.add(HasnHumans(hasn_id=hv, user_id=uv, star_id=str(uv), nickname=hv, status='active'))
    db.add(HasnEnterpriseMembership(enterprise_id=e_id, user_id=uv, role='owner', status='approved'))
    if active:
        db.add(HasnOwnerWorkbenchPref(owner_hasn_id=hv, active_enterprise_id=e_id))
    await db.flush()

    svc = PlanService()
    personal_todo = await svc.create_todo(db, owner=hv, data={'title': '个人待办', 'status': 'inbox', 'actor': 'owner'})
    ent_todo = await svc.create_todo(
        db, owner=hv, data={'title': '企业待办', 'status': 'inbox', 'actor': 'owner_decision'},
        enterprise_id=e_id,
    )
    personal_event = await svc.create_event(
        db, owner=hv, data={'title': '个人日程', 'start_at': _EV_START, 'end_at': _EV_END}
    )
    ent_event = await svc.create_event(
        db, owner=hv, data={'title': '企业会议', 'start_at': _EV_START, 'end_at': _EV_END, 'visibility': 'private'},
        enterprise_id=e_id,
    )
    return _World(
        enterprise_id=e_id,
        viewer=hv,
        personal_todo_id=int(personal_todo['id']),
        ent_todo_id=int(ent_todo['id']),
        personal_event_id=int(personal_event['id']),
        ent_event_id=int(ent_event['id']),
    )


async def test_today_personal_only_when_no_active_enterprise() -> None:
    """无活跃企业 → enterprise=None；个人组只见个人条目（enterprise_id IS NULL）。"""
    async with async_db_session() as db:
        try:
            w = await _seed(db, active=False)
            svc = PlanService()
            today = await svc.today_overview(db, owner=w.viewer, day_start=_DAY_START, day_end=_DAY_END)
            assert today['enterprise'] is None
            inbox_ids = {t['id'] for t in today['inbox']}
            assert w.personal_todo_id in inbox_ids
            assert w.ent_todo_id not in inbox_ids  # 企业待办不混入个人组（个人零破坏）
            event_ids = {e['id'] for e in today['events']}
            assert w.personal_event_id in event_ids
            assert w.ent_event_id not in event_ids  # 企业事件不混入个人日历
        finally:
            await db.rollback()


async def test_today_enterprise_group_is_separate_union() -> None:
    """有活跃企业 E → enterprise 组独立返回；个人组与企业组互不混入（不变量 #2 两条读并集）。"""
    async with async_db_session() as db:
        try:
            w = await _seed(db, active=True)
            svc = PlanService()
            today = await svc.today_overview(db, owner=w.viewer, day_start=_DAY_START, day_end=_DAY_END)
            # 个人组：只有个人条目
            assert {t['id'] for t in today['inbox']} == {w.personal_todo_id}
            assert {e['id'] for e in today['events']} == {w.personal_event_id}
            # 企业组：带 enterprise_id + 企业条目（viewer 自己的，恒可见全详情）
            ent = today['enterprise']
            assert ent is not None
            assert ent['enterprise_id'] == w.enterprise_id
            assert {t['id'] for t in ent['inbox']} == {w.ent_todo_id}
            ent_event_ids = {e['id'] for e in ent['events']}
            assert w.ent_event_id in ent_event_ids
            assert ent_event_ids == {w.ent_event_id}  # 企业组不含个人事件
            # 自己的企业会议见全详情（非忙闲裁剪）
            ent_event = next(e for e in ent['events'] if e['id'] == w.ent_event_id)
            assert ent_event['title'] == '企业会议'
        finally:
            await db.rollback()


async def test_list_enterprise_todos_member_sees_own_nonmember_empty() -> None:
    """企业待办读：成员见数据范围内（含自己）；非成员读该企业 → 空（企业隔离硬底线）。"""
    async with async_db_session() as db:
        try:
            w = await _seed(db, active=True)
            svc = PlanService()
            mine = await svc.list_enterprise_todos(db, viewer_owner_hasn_id=w.viewer, enterprise_id=w.enterprise_id)
            assert {t['id'] for t in mine} == {w.ent_todo_id}
            # 非成员（另一个随机 hasn_id 未加入 E）→ 一条不返回
            outsider = f'h_{uuid4().hex[:16]}'
            db.add(
                HasnHumans(
                    hasn_id=outsider, user_id=w.enterprise_id + 999, star_id='x', nickname='x', status='active'
                )
            )
            await db.flush()
            empty = await svc.list_enterprise_todos(db, viewer_owner_hasn_id=outsider, enterprise_id=w.enterprise_id)
            assert empty == []
        finally:
            await db.rollback()

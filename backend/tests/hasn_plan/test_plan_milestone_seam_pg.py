"""PLAN-LOOP L3：待办↔里程碑数据缝 + 里程碑轻状态 真实 PG 验证。

零 mock：真实本地 PostgreSQL(15432) 跑 PlanService；事务回滚不污染库。
需要：export DATABASE_PORT=15432。

覆盖（[06] §3.2）：
- **挂靠归属校验**：milestone_id 须属本人计划、且与待办同计划；待办未定计划则随里程碑落其计划；跨计划拒；
- **里程碑轻状态**：status↔done 双向派生（写 status 推 done / 写 done 推 status）；
- **派生进度**：get_plan 里程碑输出 todo_total/todo_done/progress_pct（其下待办完成率，实时算）。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.hasn_plan.service.plan_app_service import PlanService
from backend.common.exception import errors
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')


def _owner() -> str:
    return f'hasnOwner_{uuid4().hex[:18]}'


# ── L3-a：挂靠归属校验 ────────────────────────────────────────────────────────
async def test_todo_milestone_link_autofills_plan() -> None:
    """待办未定 plan_id + 指定 milestone_id → 自动随里程碑落到其计划。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            plan = await svc.create_plan(db, owner=owner, data={'title': '计划A'})
            ms = await svc.create_milestone(db, owner=owner, plan_id=int(plan['id']), data={'title': '里程碑M'})
            todo = await svc.create_todo(db, owner=owner, data={'title': '挂靠', 'milestone_id': int(ms['id'])})
            assert int(todo['milestone_id']) == int(ms['id'])
            assert int(todo['plan_id']) == int(plan['id'])  # plan_id 随里程碑自动补全
        finally:
            await db.rollback()


async def test_todo_milestone_same_plan_ok() -> None:
    """待办显式同计划 + 里程碑同计划 → 通过。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            plan = await svc.create_plan(db, owner=owner, data={'title': '计划A'})
            ms = await svc.create_milestone(db, owner=owner, plan_id=int(plan['id']), data={'title': 'M'})
            todo = await svc.create_todo(
                db, owner=owner, data={'title': 't', 'plan_id': int(plan['id']), 'milestone_id': int(ms['id'])}
            )
            assert int(todo['milestone_id']) == int(ms['id'])
        finally:
            await db.rollback()


async def test_todo_milestone_cross_plan_rejected() -> None:
    """待办属计划B + 里程碑属计划A → milestone_plan_mismatch。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            plan_a = await svc.create_plan(db, owner=owner, data={'title': '计划A'})
            plan_b = await svc.create_plan(db, owner=owner, data={'title': '计划B'})
            ms_a = await svc.create_milestone(db, owner=owner, plan_id=int(plan_a['id']), data={'title': 'MA'})
            with pytest.raises(errors.RequestError) as ei:
                await svc.create_todo(
                    db,
                    owner=owner,
                    data={'title': 't', 'plan_id': int(plan_b['id']), 'milestone_id': int(ms_a['id'])},
                )
            assert ei.value.data['error_code'] == 'milestone_plan_mismatch'
        finally:
            await db.rollback()


async def test_todo_milestone_not_found_rejected() -> None:
    """指定不存在的 milestone_id → milestone_not_found。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            with pytest.raises(errors.RequestError) as ei:
                await svc.create_todo(db, owner=owner, data={'title': 't', 'milestone_id': 987654321})
            assert ei.value.data['error_code'] == 'milestone_not_found'
        finally:
            await db.rollback()


async def test_todo_milestone_foreign_owner_rejected() -> None:
    """里程碑属**他人**计划 → 经计划归属校验拒（NotFound，跨 owner 不可挂）。"""
    owner_a, owner_b = _owner(), _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            plan_b = await svc.create_plan(db, owner=owner_b, data={'title': '他人计划'})
            ms_b = await svc.create_milestone(db, owner=owner_b, plan_id=int(plan_b['id']), data={'title': 'MB'})
            with pytest.raises(errors.NotFoundError):
                await svc.create_todo(db, owner=owner_a, data={'title': 't', 'milestone_id': int(ms_b['id'])})
        finally:
            await db.rollback()


async def test_todo_milestone_update_link_and_unbind() -> None:
    """update：可后挂里程碑；显式置 None 解绑跳过校验。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            plan = await svc.create_plan(db, owner=owner, data={'title': '计划A'})
            ms = await svc.create_milestone(db, owner=owner, plan_id=int(plan['id']), data={'title': 'M'})
            todo = await svc.create_todo(db, owner=owner, data={'title': 't', 'plan_id': int(plan['id'])})
            linked = await svc.update_todo(db, owner=owner, pk=todo['id'], data={'milestone_id': int(ms['id'])})
            assert int(linked['milestone_id']) == int(ms['id'])
            unbound = await svc.update_todo(db, owner=owner, pk=todo['id'], data={'milestone_id': None})
            assert unbound['milestone_id'] is None
        finally:
            await db.rollback()


# ── L3-b：里程碑轻状态 status↔done 双向派生 ──────────────────────────────────
async def test_milestone_status_default_planned() -> None:
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            plan = await svc.create_plan(db, owner=owner, data={'title': 'P'})
            ms = await svc.create_milestone(db, owner=owner, plan_id=int(plan['id']), data={'title': 'M'})
            assert ms['status'] == 'planned'
            assert ms['done'] is False
        finally:
            await db.rollback()


async def test_milestone_status_done_derives_done_true() -> None:
    """写 status='done' → done=true 派生。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            plan = await svc.create_plan(db, owner=owner, data={'title': 'P'})
            ms = await svc.create_milestone(
                db, owner=owner, plan_id=int(plan['id']), data={'title': 'M', 'status': 'done'}
            )
            assert ms['status'] == 'done'
            assert ms['done'] is True
        finally:
            await db.rollback()


async def test_milestone_done_flag_derives_status() -> None:
    """写 done=true（无 status）→ status='done' 反向派生；done=false → 'planned'。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            plan = await svc.create_plan(db, owner=owner, data={'title': 'P'})
            ms = await svc.create_milestone(db, owner=owner, plan_id=int(plan['id']), data={'title': 'M', 'done': True})
            assert ms['status'] == 'done'
            assert ms['done'] is True
            # update done=False → status 回 planned
            reverted = await svc.update_milestone(db, owner=owner, milestone_id=int(ms['id']), data={'done': False})
            assert reverted['status'] == 'planned'
            assert reverted['done'] is False
        finally:
            await db.rollback()


async def test_milestone_update_status_doing() -> None:
    """update status='doing' → done=false（doing 非完成态）。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            plan = await svc.create_plan(db, owner=owner, data={'title': 'P'})
            ms = await svc.create_milestone(db, owner=owner, plan_id=int(plan['id']), data={'title': 'M'})
            upd = await svc.update_milestone(db, owner=owner, milestone_id=int(ms['id']), data={'status': 'doing'})
            assert upd['status'] == 'doing'
            assert upd['done'] is False
        finally:
            await db.rollback()


# ── L3-c：get_plan 派生进度 ───────────────────────────────────────────────────
async def test_get_plan_milestone_derived_progress() -> None:
    """里程碑派生进度 = 其下待办完成率：3 待办 2 done → progress_pct=67。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            plan = await svc.create_plan(db, owner=owner, data={'title': 'P'})
            ms = await svc.create_milestone(db, owner=owner, plan_id=int(plan['id']), data={'title': 'M'})
            mid = int(ms['id'])
            # create 直接落 status（create 不过状态机），2 done + 1 todo
            for st in ('done', 'done', 'todo'):
                await svc.create_todo(
                    db,
                    owner=owner,
                    data={'title': f't-{st}', 'plan_id': int(plan['id']), 'milestone_id': mid, 'status': st},
                )
            detail = await svc.get_plan(db, owner=owner, pk=int(plan['id']))
            m = next(x for x in detail['milestones'] if int(x['id']) == mid)
            assert m['todo_total'] == 3
            assert m['todo_done'] == 2
            assert m['progress_pct'] == 67  # round(2/3*100)
        finally:
            await db.rollback()


async def test_get_plan_empty_milestone_zero_progress() -> None:
    """无待办的里程碑 → progress_pct=0（不因 total=0 误判 100%）。"""
    owner = _owner()
    svc = PlanService()
    async with async_db_session() as db:
        try:
            plan = await svc.create_plan(db, owner=owner, data={'title': 'P'})
            ms = await svc.create_milestone(db, owner=owner, plan_id=int(plan['id']), data={'title': 'M'})
            detail = await svc.get_plan(db, owner=owner, pk=int(plan['id']))
            m = next(x for x in detail['milestones'] if int(x['id']) == int(ms['id']))
            assert m['todo_total'] == 0
            assert m['todo_done'] == 0
            assert m['progress_pct'] == 0
        finally:
            await db.rollback()

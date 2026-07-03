"""PLAN-ENT A1：规划应用双模化（个人 + 企业日历）数据层真实 PG 验证。

零 mock：真实本地 PostgreSQL(15432) 跑 PlanService + EventAttendee 模型；事务回滚不污染库。
需要：export DATABASE_PORT=15432。

覆盖（施工清单 A1 pytest 项）：
- 个人路径（enterprise_id IS NULL）CRUD 与改造前一致（冻结不变量 #1「个人零破坏」回归）；
- 企业条目建/读带 enterprise_id/dept_id + 企业维度直查隔离（不变量 #2）；
- event_attendee 建/RSVP 往返 + UNIQUE(event_id, attendee_hasn_id) 冲突 + FK CASCADE；
- 归属不可变（不变量 #3：update 不改 enterprise_id）；参会人 enterprise_id NOT NULL（不变量 #4）；
- 迁移列宽/幂等 DDL 语句级验证。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa

from sqlalchemy.exc import IntegrityError

from backend.app.hasn_plan.model import Event, EventAttendee
from backend.app.hasn_plan.service.plan_app_service import PlanService
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='module')

_ENT_A = 90001  # 逻辑企业 id A（本测试专用，逻辑引用无硬 FK）
_ENT_B = 90002  # 逻辑企业 id B
_DEPT = 70001


async def test_personal_path_zero_breakage() -> None:
    """个人路径：不传 enterprise_id → 归属列 NULL，与 [01] 现状完全一致（不变量 #1）。"""
    owner = f'hasnOwner_{uuid4().hex[:18]}'
    svc = PlanService()
    async with async_db_session() as db:
        try:
            todo = await svc.create_todo(db, owner=owner, data={'title': '个人待办', 'actor': 'owner'})
            assert todo['enterprise_id'] is None
            assert todo['dept_id'] is None

            ev = await svc.create_event(
                db,
                owner=owner,
                data={
                    'title': '个人日程',
                    'start_at': '2026-07-02T09:00:00+08:00',
                    'end_at': '2026-07-02T10:00:00+08:00',
                },
            )
            assert ev['enterprise_id'] is None
            assert ev['visibility'] == 'private'  # 个人事件恒 private（DB 默认）

            # list_events（owner 隔离）照常返回，不受双模列影响
            rows = await svc.list_events(db, owner=owner)
            assert any(r['id'] == ev['id'] for r in rows)
        finally:
            await db.rollback()


async def test_enterprise_ownership_and_isolation() -> None:
    """企业条目：服务端注入 enterprise_id/dept_id 落库读回；企业维度直查恒前置 enterprise_id 隔离（不变量 #2）。"""
    owner = f'hasnOwner_{uuid4().hex[:18]}'
    svc = PlanService()
    async with async_db_session() as db:
        try:
            ev = await svc.create_event(
                db,
                owner=owner,
                data={
                    'title': '部门周会',
                    'kind': 'fixed',
                    'actor': 'attend',
                    'start_at': '2026-07-03T14:00:00+08:00',
                    'end_at': '2026-07-03T15:00:00+08:00',
                    'source': 'oa_meeting',
                    'visibility': 'public',
                },
                enterprise_id=_ENT_A,
                dept_id=_DEPT,
            )
            assert ev['enterprise_id'] == _ENT_A
            assert ev['dept_id'] == _DEPT
            assert ev['source'] == 'oa_meeting'  # 新字典值可写
            assert ev['visibility'] == 'public'

            # 企业维度直查（恒前置 enterprise_id==E）——命中 A，不命中 B
            hit = (
                (await db.execute(sa.select(Event).where(Event.enterprise_id == _ENT_A, Event.id == ev['id'])))
                .scalars()
                .first()
            )
            assert hit is not None
            miss = (
                (await db.execute(sa.select(Event).where(Event.enterprise_id == _ENT_B, Event.id == ev['id'])))
                .scalars()
                .first()
            )
            assert miss is None
        finally:
            await db.rollback()


async def test_ownership_immutable_on_update() -> None:
    """不变量 #3：enterprise_id 建后不改——update 白名单不含 enterprise_id，客户端塞了也被丢弃。"""
    owner = f'hasnOwner_{uuid4().hex[:18]}'
    svc = PlanService()
    async with async_db_session() as db:
        try:
            ev = await svc.create_event(
                db,
                owner=owner,
                data={
                    'title': '企业事件',
                    'start_at': '2026-07-04T09:00:00+08:00',
                    'end_at': '2026-07-04T10:00:00+08:00',
                },
                enterprise_id=_ENT_A,
            )
            # 客户端在 update data 里塞 enterprise_id / dept_id → 被 _pick 白名单丢弃
            updated = await svc.update_event(
                db, owner=owner, pk=ev['id'], data={'title': '改标题', 'enterprise_id': _ENT_B, 'dept_id': _DEPT}
            )
            assert updated['title'] == '改标题'
            assert updated['enterprise_id'] == _ENT_A  # 未被篡改
            assert updated['dept_id'] is None
        finally:
            await db.rollback()


async def test_event_attendee_roundtrip_unique_and_cascade() -> None:
    """event_attendee：建/RSVP 往返 + UNIQUE(event_id, attendee) 冲突 + event 删除级联。"""
    owner = f'hasnOwner_{uuid4().hex[:18]}'
    attendee = f'hasnPeer_{uuid4().hex[:18]}'
    svc = PlanService()
    async with async_db_session() as db:
        try:
            ev = await svc.create_event(
                db,
                owner=owner,
                data={
                    'title': '企业会议',
                    'start_at': '2026-07-05T09:00:00+08:00',
                    'end_at': '2026-07-05T10:00:00+08:00',
                },
                enterprise_id=_ENT_A,
            )
            row = EventAttendee(
                event_id=ev['id'], enterprise_id=_ENT_A, attendee_hasn_id=attendee, role='required', rsvp='none'
            )
            db.add(row)
            await db.flush()
            assert row.id is not None
            assert row.rsvp == 'none'

            # RSVP 回执往返
            row.rsvp = 'accepted'
            await db.flush()
            reread = (await db.execute(sa.select(EventAttendee).where(EventAttendee.id == row.id))).scalars().first()
            assert reread.rsvp == 'accepted'

            # UNIQUE(event_id, attendee_hasn_id) 冲突
            dup = EventAttendee(event_id=ev['id'], enterprise_id=_ENT_A, attendee_hasn_id=attendee)
            db.add(dup)
            with pytest.raises(IntegrityError):
                await db.flush()
        finally:
            await db.rollback()

    # FK CASCADE：删 event → attendee 随删（独立事务，避免上面 rollback 干扰）
    async with async_db_session() as db:
        try:
            ev = await svc.create_event(
                db,
                owner=owner,
                data={
                    'title': '待删会议',
                    'start_at': '2026-07-06T09:00:00+08:00',
                    'end_at': '2026-07-06T10:00:00+08:00',
                },
                enterprise_id=_ENT_A,
            )
            row = EventAttendee(event_id=ev['id'], enterprise_id=_ENT_A, attendee_hasn_id=attendee)
            db.add(row)
            await db.flush()
            attendee_pk = row.id

            await svc.delete_event(db, owner=owner, pk=ev['id'])
            await db.flush()
            gone = (await db.execute(sa.select(EventAttendee).where(EventAttendee.id == attendee_pk))).scalars().first()
            assert gone is None  # ON DELETE CASCADE 生效
        finally:
            await db.rollback()


async def test_attendee_enterprise_id_not_null() -> None:
    """不变量 #4 底座：event_attendee.enterprise_id NOT NULL（冗余企业维度恒有值）。

    经原生 INSERT 省略 enterprise_id（模型 default=0 会掩盖 DB 约束，故绕过 ORM 直打 DDL 约束）。
    """
    owner = f'hasnOwner_{uuid4().hex[:18]}'
    svc = PlanService()
    async with async_db_session() as db:
        try:
            ev = await svc.create_event(
                db,
                owner=owner,
                data={'title': '会议', 'start_at': '2026-07-07T09:00:00+08:00', 'end_at': '2026-07-07T10:00:00+08:00'},
                enterprise_id=_ENT_A,
            )
            with pytest.raises(IntegrityError):
                await db.execute(
                    sa.text(
                        'INSERT INTO hasn_plan.event_attendee (event_id, attendee_hasn_id, role, rsvp) '
                        'VALUES (:eid, :who, :role, :rsvp)'
                    ),
                    {'eid': ev['id'], 'who': 'x', 'role': 'required', 'rsvp': 'none'},
                )
        finally:
            await db.rollback()


async def test_migration_ddl_idempotent() -> None:
    """迁移 DDL 幂等（语句级）：ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS 连跑两次不炸。"""
    async with async_db_session() as db:
        try:
            stmts = [
                'ALTER TABLE hasn_plan.event ADD COLUMN IF NOT EXISTS enterprise_id bigint',
                'ALTER TABLE hasn_plan.event ADD COLUMN IF NOT EXISTS visibility varchar(16) '
                "NOT NULL DEFAULT 'private'",
                'CREATE INDEX IF NOT EXISTS idx_plan_event_ent ON hasn_plan.event (enterprise_id, start_at)',
            ]
            for stmt in stmts:
                await db.execute(sa.text(stmt))
                await db.execute(sa.text(stmt))  # 第二遍幂等
        finally:
            await db.rollback()

"""PLAN-TRIAGE A2：到期分诊数据层（todo.actor 四态 + owner_decision + 留痕三列）真实 PG 验证。

零 mock：真实本地 PostgreSQL(15432) 跑 PlanService + Todo 模型；事务回滚不污染库。
需要：export DATABASE_PORT=15432。

覆盖（施工清单 A2 pytest 项）：
- `todo.create actor='owner_decision'` 落库读回（不变量 #8「决策≠亲手做」：可派发的待决策态）；
- actor 非法值仍拒——数据层的权威闸门是列宽 varchar(16)（超长即拒）；
  **枚举值校验（拒未知 actor 串）是 A4 工具面职责**（施工清单 A4「actor 校验放行」），
  数据层不做枚举白名单，故此处只断言列宽边界（owner_decision=14 入得、17 字符拒）；
- decision_note/completion_note/cancel_reason 往返（create + update），且独立于 notes 用户备注；
- 迁移幂等（语句级）：ALTER COLUMN TYPE / ADD COLUMN IF NOT EXISTS / COMMENT ON 连跑两次不炸。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa

from sqlalchemy.exc import DBAPIError

from backend.app.hasn_plan.service.plan_app_service import PlanService
from backend.database.db import async_db_session

pytestmark = pytest.mark.asyncio(loop_scope='session')


async def test_todo_actor_owner_decision_roundtrip() -> None:
    """actor='owner_decision'（14 字符）落库读回——列宽 varchar(16) 扩宽后可容纳（不变量 #8）。"""
    owner = f'hasnOwner_{uuid4().hex[:18]}'
    svc = PlanService()
    async with async_db_session() as db:
        try:
            todo = await svc.create_todo(db, owner=owner, data={'title': '要不要签这份合同', 'actor': 'owner_decision'})
            assert todo['actor'] == 'owner_decision'

            reread = await svc.get_todo(db, owner=owner, pk=todo['id'])
            assert reread['actor'] == 'owner_decision'
        finally:
            await db.rollback()


async def test_todo_actor_overlength_rejected() -> None:
    """actor 非法值仍拒（数据层口径）：超过 varchar(16) 的 actor 串被 PG 拒绝。

    说明：数据层不做枚举白名单校验（拒未知合法长度串是 A4 工具面职责）；此处证明列宽是硬边界，
    确保 owner_decision（14）恰好装得下、更长的枚举将来必须再次扩宽。
    """
    owner = f'hasnOwner_{uuid4().hex[:18]}'
    svc = PlanService()
    async with async_db_session() as db:
        try:
            # asyncpg 的 StringDataRightTruncationError 被 SQLAlchemy 包成父类 DBAPIError（DataError 的父）。
            with pytest.raises(DBAPIError, match='too long'):
                await svc.create_todo(db, owner=owner, data={'title': '超长归属', 'actor': 'x' * 17})
        finally:
            await db.rollback()


async def test_todo_triage_note_columns_roundtrip() -> None:
    """decision_note / completion_note / cancel_reason 往返，且与 notes 用户备注各自独立。"""
    owner = f'hasnOwner_{uuid4().hex[:18]}'
    svc = PlanService()
    async with async_db_session() as db:
        try:
            # create：四个文本列并存、互不覆盖
            todo = await svc.create_todo(
                db,
                owner=owner,
                data={
                    'title': '季度定价决策',
                    'actor': 'owner_decision',
                    'notes': '用户随手记的备注',
                    'decision_note': '拍板：涨价 8%，理由是成本上行',
                    'completion_note': '已通知全体销售',
                    'cancel_reason': '',
                },
            )
            assert todo['notes'] == '用户随手记的备注'
            assert todo['decision_note'] == '拍板：涨价 8%，理由是成本上行'
            assert todo['completion_note'] == '已通知全体销售'
            # 精确断言空串（而非 `not ...`）：验证 '' 原样往返、未被强制成 None。
            assert todo['cancel_reason'] == ''  # noqa: PLC1901

            # update：三留痕列可独立更新，不动 notes
            updated = await svc.update_todo(
                db,
                owner=owner,
                pk=todo['id'],
                data={
                    'status': 'cancelled',
                    'cancel_reason': '客户临时取消需求',
                    'decision_note': '维持原价',
                },
            )
            assert updated['status'] == 'cancelled'
            assert updated['cancel_reason'] == '客户临时取消需求'
            assert updated['decision_note'] == '维持原价'
            assert updated['completion_note'] == '已通知全体销售'  # 未在 update 中出现 → 保持不变
            assert updated['notes'] == '用户随手记的备注'  # notes 与留痕列解耦
        finally:
            await db.rollback()


async def test_todo_actor_migration_idempotent() -> None:
    """迁移 DDL 幂等（语句级）：扩列宽 / ADD COLUMN IF NOT EXISTS / COMMENT ON 连跑两次不炸。"""
    async with async_db_session() as db:
        try:
            stmts = [
                'ALTER TABLE hasn_plan.todo ALTER COLUMN actor TYPE varchar(16)',
                'ALTER TABLE hasn_plan.todo ADD COLUMN IF NOT EXISTS decision_note text NULL',
                'ALTER TABLE hasn_plan.todo ADD COLUMN IF NOT EXISTS completion_note text NULL',
                'ALTER TABLE hasn_plan.todo ADD COLUMN IF NOT EXISTS cancel_reason text NULL',
                "COMMENT ON COLUMN hasn_plan.todo.decision_note IS 'owner_decision 决策留痕'",
            ]
            for stmt in stmts:
                await db.execute(sa.text(stmt))
                await db.execute(sa.text(stmt))  # 第二遍幂等
        finally:
            await db.rollback()

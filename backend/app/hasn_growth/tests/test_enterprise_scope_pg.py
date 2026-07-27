"""获客 enterprise 化 GE1 数据层双模化真实 PG 测试（零 mock，回滚）。

覆盖（实施 92 GE1 / 设计 v3 §6.7）：
- 迁移幂等：同一连接连跑两遍 ADD COLUMN/CREATE INDEX，不报错、列与索引齐全；
- 存量零回退：不显式给 owner_scope 的新行默认 'personal'，enterprise_id/assignee 为 NULL；
- 双模归属：enterprise 行能落 enterprise_id + assignee；
- 唯一约束双模：
  * personal 同 (user_id, lead_contact_id) 重复被拒；
  * enterprise 同 lead 与 personal 不冲突（不同 partial unique）；
  * 同企业同 lead 重复被拒。

需要 export DATABASE_PORT=15432。事务内 savepoint 隔离预期冲突，整体回滚不污染库。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_growth.model.customer import Customer
from backend.app.hasn_growth.model.lead_contact import LeadContact
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[4]
_MIGRATION_SQL = _REPO / 'backend/sql/hasn_growth/migrations/2026-06-15-enterprise-scope.sql'


async def _apply_migration(session) -> None:
    """经底层 asyncpg 连接跑迁移 SQL（simple query 协议，多语句）。"""
    raw = await (await session.connection()).get_raw_connection()
    await raw.driver_connection.execute(_MIGRATION_SQL.read_text(encoding='utf-8'))


@pytest_asyncio.fixture
async def session() -> AsyncIterator:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def _make_lead(session, *, tag: str) -> LeadContact:
    # 统一线索池：contact 无 user_id（公共池行）。本 helper 仅为 Customer 测试提供 lead_contact_id FK。
    lead = LeadContact(
        lead_no=f'LGE1{tag}', company_name='Acme',
        contact_name='王五', source_type='manual', status='valid', confidence_score=Decimal(70),
    )
    session.add(lead)
    await session.flush()
    return lead


async def test_migration_idempotent_and_columns_present(session) -> None:
    # 连跑两遍迁移：不报错（幂等）
    await _apply_migration(session)
    await _apply_migration(session)
    for tbl in ('customer', 'opportunity', 'outreach_message', 'activity', 'form_submission'):
        cols = set(
            (
                await session.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='hasn_growth' AND table_name=:t "
                        "AND column_name IN ('owner_scope','enterprise_id','assignee')"
                    ),
                    {'t': tbl},
                )
            ).scalars().all()
        )
        assert cols == {'owner_scope', 'enterprise_id', 'assignee'}, f'{tbl} 缺企业化列：{cols}'
    pb = set(
        (
            await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='hasn_growth' AND table_name='playbook' "
                    "AND column_name IN ('owner_scope','enterprise_id')"
                )
            )
        ).scalars().all()
    )
    assert pb == {'owner_scope', 'enterprise_id'}


async def test_existing_row_defaults_personal(session) -> None:
    await _apply_migration(session)
    lead = await _make_lead(session, tag='df')
    # 不显式给 owner_scope —— 应落 DB DEFAULT 'personal'
    cust = Customer(customer_no='GE1DF1', user_id=951001, lead_contact_id=lead.id, source_kind='manual', lifecycle_status='active')
    session.add(cust)
    await session.flush()
    await session.refresh(cust)
    assert cust.owner_scope == 'personal'
    assert cust.enterprise_id is None
    assert cust.assignee is None


async def test_enterprise_row_carries_holder_and_assignee(session) -> None:
    await _apply_migration(session)
    lead = await _make_lead(session, tag='en')
    cust = Customer(
        customer_no='GE1EN1', user_id=951002, lead_contact_id=lead.id, source_kind='manual',
        lifecycle_status='active', owner_scope='enterprise', enterprise_id=8801, assignee='h_sales_a',
    )
    session.add(cust)
    await session.flush()
    await session.refresh(cust)
    assert cust.owner_scope == 'enterprise'
    assert cust.enterprise_id == 8801
    assert cust.assignee == 'h_sales_a'


async def test_dual_mode_unique_constraints(session) -> None:
    await _apply_migration(session)
    lead = await _make_lead(session, tag='uq')

    # personal 首条
    session.add(Customer(customer_no='GE1UQ_P1', user_id=951003, lead_contact_id=lead.id, source_kind='manual', lifecycle_status='active'))
    await session.flush()

    # personal 同 (user_id, lead) 重复 → 被拒（savepoint 隔离）
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(Customer(customer_no='GE1UQ_P2', user_id=951003, lead_contact_id=lead.id, source_kind='manual', lifecycle_status='active'))
            await session.flush()

    # enterprise 同 lead 与 personal 不冲突（不同 partial unique）
    session.add(Customer(customer_no='GE1UQ_E1', user_id=951003, lead_contact_id=lead.id, source_kind='manual', lifecycle_status='active', owner_scope='enterprise', enterprise_id=8802, assignee='h_a'))
    await session.flush()

    # 同企业同 lead 重复 → 被拒
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            session.add(Customer(customer_no='GE1UQ_E2', user_id=951003, lead_contact_id=lead.id, source_kind='manual', lifecycle_status='active', owner_scope='enterprise', enterprise_id=8802, assignee='h_b'))
            await session.flush()

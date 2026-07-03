"""获客企业开通自播种 GE3 真实 PG 测试（零 mock，回滚）。

覆盖 §92 GE3 验收：
- 企业首次开通获客（grant_entitlement subject_type='enterprise' app_id='growth'）→ 自播种企业 playbook；
- 重复开通幂等不重复；
- 个人开通（subject_type='owner'）不产生企业 playbook；
- 企业 playbook 仅本企业成员经 list_for_owner 可见（个人上下文看不到）。
需要 export DATABASE_PORT=15432。本测试在事务内造数 + 调 service，结束回滚不污染库。
"""

from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core.app_platform import grant_entitlement
from backend.app.hasn_growth.model.playbook import Playbook
from backend.app.hasn_growth.service.enterprise_seed_service import ensure_growth_enterprise_seeded
from backend.app.hasn_growth.service.playbook_service import playbook_service
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio


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


async def _builtin_count(session) -> int:
    return (
        await session.execute(
            sa.select(sa.func.count()).select_from(Playbook).where(
                Playbook.is_builtin.is_(True), Playbook.user_id.is_(None)
            )
        )
    ).scalar_one()


async def _enterprise_playbooks(session, enterprise_id: int) -> list[Playbook]:
    return list(
        (
            await session.execute(
                sa.select(Playbook).where(
                    Playbook.owner_scope == 'enterprise', Playbook.enterprise_id == enterprise_id
                )
            )
        ).scalars().all()
    )


async def test_enterprise_grant_seeds_playbooks_idempotent(session) -> None:
    tag = uuid.uuid4().hex[:8]
    ent_id = 7800000 + int(uuid.uuid4().int % 90000)

    # 造两套唯一命名的内置模板（不依赖库里既有内置；与真实内置共同构成模板集）。
    fixture_names = {f'内置打法A·{tag}', f'内置打法B·{tag}'}
    for nm in fixture_names:
        session.add(Playbook(user_id=None, name=nm, is_builtin=True, owner_scope='personal'))
    await session.flush()

    n_builtin = await _builtin_count(session)
    assert n_builtin >= 2

    # 首次企业开通获客 → 自播种（经 grant_entitlement 钩子，走真实授权路径）。
    await grant_entitlement(
        db=session, app_id='growth', subject_type='enterprise', subject_id=str(ent_id), source='admin_grant'
    )
    seeded = await _enterprise_playbooks(session, ent_id)
    assert len(seeded) == n_builtin, '企业 playbook 应为每个内置模板各一份'
    seeded_names = {p.name for p in seeded}
    assert fixture_names <= seeded_names, '我的内置模板应被复制为企业 playbook'
    for p in seeded:
        assert p.owner_scope == 'enterprise' and p.enterprise_id == ent_id
        assert p.is_builtin is False and p.user_id is None  # 企业副本可改、非全局内置

    # 重复开通 → 幂等不重复。
    await grant_entitlement(
        db=session, app_id='growth', subject_type='enterprise', subject_id=str(ent_id), source='purchase'
    )
    again = await _enterprise_playbooks(session, ent_id)
    assert len(again) == len(seeded), '重复开通不应再播种'

    # 直接调 service 再跑一遍亦幂等（新增 0）。
    added = await ensure_growth_enterprise_seeded(session, enterprise_id=ent_id)
    assert added == 0


async def test_personal_grant_does_not_seed_enterprise(session) -> None:
    ent_id = 7900000 + int(uuid.uuid4().int % 90000)
    # 个人开通（owner 维度）→ 不触发企业播种。
    await grant_entitlement(
        db=session, app_id='growth', subject_type='owner', subject_id=f'h_solo_{uuid.uuid4().hex[:6]}', source='trial'
    )
    assert await _enterprise_playbooks(session, ent_id) == []

    # 非 growth 应用的企业开通 → 不触发获客播种。
    await grant_entitlement(
        db=session, app_id='knowledge', subject_type='enterprise', subject_id=str(ent_id), source='admin_grant'
    )
    assert await _enterprise_playbooks(session, ent_id) == []


async def test_enterprise_playbooks_visibility(session) -> None:
    tag = uuid.uuid4().hex[:8]
    ent_id = 7950000 + int(uuid.uuid4().int % 40000)
    member_uid = 960000 + int(uuid.uuid4().int % 9000)
    uniq = f'企业独有打法·{tag}'

    # 直接造一条企业 playbook（模拟已播种）。
    session.add(
        Playbook(user_id=None, name=uniq, is_builtin=False, owner_scope='enterprise', enterprise_id=ent_id)
    )
    await session.flush()

    # 企业成员（带 enterprise_id）可见；个人上下文（enterprise_id=None）不可见。
    ent_view = await playbook_service.list_for_owner(session, user_id=member_uid, enterprise_id=ent_id)
    assert any(p['name'] == uniq and p['owner_scope'] == 'enterprise' for p in ent_view)

    personal_view = await playbook_service.list_for_owner(session, user_id=member_uid, enterprise_id=None)
    assert all(p['name'] != uniq for p in personal_view), '个人上下文不应看到企业 playbook'

    # 别的企业成员也看不到本企业 playbook。
    other_view = await playbook_service.list_for_owner(session, user_id=member_uid, enterprise_id=ent_id + 1)
    assert all(p['name'] != uniq for p in other_view)

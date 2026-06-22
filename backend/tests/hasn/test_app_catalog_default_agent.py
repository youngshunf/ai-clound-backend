"""AppCollab AC-P1 真实 PG 数据层测试（零 mock）。

覆盖 doc21 §4.3/§5.4/§7.2：
  1) catalog 播种回填「默认承接分身类型 + 业务提示词」——deck/designsystem/creator/film 同绑
     content_operator 且 prompt 非空；knowledge 绑 assistant（应用中心改版：原 NULL → assistant）。
  2) resolve_default_agent_for_app：default_agent_type 命中 builtin_agent_key → 返该分身；
     无匹配 → 回退主脑（pref.primary_agent_id → role=primary → 首个活跃）；
     无任何活跃分身 → None（诚实空态）。

需要本地 PostgreSQL（export DATABASE_PORT=15432）。不可达则 skip。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service.app_catalog_service import (
    ensure_catalog_seeded,
    resolve_default_agent_for_app,
)
from backend.app.workbench.model.hasn_owner_workbench_pref import HasnOwnerWorkbenchPref
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_BOUND_APPS = ('deck', 'designsystem', 'creator', 'film')


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        # AC-P1 两列在迁移里加（IF NOT EXISTS 幂等）——测试自带 DDL 兜底，使 schema 与 model 对齐。
        async with engine.begin() as conn:
            await conn.execute(select(1))
            await conn.execute(
                sa.text('ALTER TABLE hasn_app_catalog ADD COLUMN IF NOT EXISTS default_agent_type VARCHAR(64)')
            )
            await conn.execute(
                sa.text('ALTER TABLE hasn_app_catalog ADD COLUMN IF NOT EXISTS work_session_system_prompt TEXT')
            )
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    db = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield db
    finally:
        await db.rollback()
        await db.close()
        await engine.dispose()


async def _make_agent(
    db,
    *,
    owner_id: str,
    suffix: str,
    builtin_key: str | None,
    role: str,
    uid: int,
) -> str:
    hasn_id = f'a_{suffix}_{_uid()}'
    db.add(
        HasnAgents(
            hasn_id=hasn_id,
            star_id=f'{uid}#{suffix}',
            owner_id=owner_id,
            display_name=f'Agent {suffix}',
            agent_name=f'agent_{hasn_id}',
            type='cloud',
            runtime_location='cloud',
            role=role,
            builtin_agent_key=builtin_key,
            api_key_hash='hash',
            status='active',
            created_via='client',
            profile_revision=1,
        )
    )
    return hasn_id


async def _make_owner(db) -> tuple[str, int]:
    tag = _uid()
    owner_id = f'h_owner_{tag}'
    uid = 980000 + int(uuid.uuid4().int % 9000)
    db.add(HasnHumans(hasn_id=owner_id, star_id=f's_{uid}', user_id=uid, nickname=f'O_{tag}', status='active'))
    await db.flush()
    return owner_id, uid


async def test_catalog_seed_populates_default_agent_type(session) -> None:
    # 删相关行后重播种 → 走「新插入」路径，断言静态默认表生效（不依赖既有迁移 UPDATE）。
    # knowledge 一并删除：应用中心改版后它绑 assistant，需走重插入才能断言新默认（INSERT-only 不回写存量）。
    seed_apps = (*_BOUND_APPS, 'knowledge')
    await session.execute(sa.delete(HasnAppCatalog).where(HasnAppCatalog.app_id.in_(seed_apps)))
    await session.flush()

    await ensure_catalog_seeded(session)

    rows = {
        r.app_id: r
        for r in (
            await session.execute(select(HasnAppCatalog).where(HasnAppCatalog.app_id.in_(seed_apps)))
        )
        .scalars()
        .all()
    }
    for app_id in _BOUND_APPS:
        assert rows[app_id].default_agent_type == 'content_operator', app_id
        assert rows[app_id].work_session_system_prompt, app_id
    # 应用中心改版：knowledge 现绑「全能助理（assistant）」（此前未列出 → NULL 回退主脑）。
    assert rows['knowledge'].default_agent_type == 'assistant'
    assert rows['knowledge'].work_session_system_prompt


async def test_resolve_returns_matching_builtin_agent(session) -> None:
    owner_id, uid = await _make_owner(session)
    # 主脑（role=primary，但非 content_operator）+ 内容运营官（builtin_key=content_operator）。
    await _make_agent(session, owner_id=owner_id, suffix='primary', builtin_key=None, role='primary', uid=uid)
    operator = await _make_agent(
        session, owner_id=owner_id, suffix='ops', builtin_key='content_operator', role='specialist', uid=uid
    )
    await session.flush()

    # 确保 deck 行 default_agent_type=content_operator（删后播种回填）。
    await session.execute(sa.delete(HasnAppCatalog).where(HasnAppCatalog.app_id == 'deck'))
    await session.flush()
    await ensure_catalog_seeded(session)

    resolved = await resolve_default_agent_for_app(session, owner_id=owner_id, app_id='deck')
    assert resolved == operator  # 命中类型键 → 返内容运营官，而非主脑


async def test_resolve_falls_back_to_primary_when_no_match(session) -> None:
    owner_id, uid = await _make_owner(session)
    # 无 content_operator；有主脑指针 + role=primary。
    primary = await _make_agent(
        session, owner_id=owner_id, suffix='primary', builtin_key=None, role='primary', uid=uid
    )
    await _make_agent(session, owner_id=owner_id, suffix='other', builtin_key='reporter', role='specialist', uid=uid)
    session.add(HasnOwnerWorkbenchPref(owner_hasn_id=owner_id, primary_agent_id=primary))
    await session.flush()

    await session.execute(sa.delete(HasnAppCatalog).where(HasnAppCatalog.app_id == 'deck'))
    await session.flush()
    await ensure_catalog_seeded(session)

    resolved = await resolve_default_agent_for_app(session, owner_id=owner_id, app_id='deck')
    assert resolved == primary  # 无匹配类型 → 回退主脑指针


async def test_resolve_returns_none_when_no_agents(session) -> None:
    owner_id, _uid_ = await _make_owner(session)  # 无任何分身
    resolved = await resolve_default_agent_for_app(session, owner_id=owner_id, app_id='deck')
    assert resolved is None

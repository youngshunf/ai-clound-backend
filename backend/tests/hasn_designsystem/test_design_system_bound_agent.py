"""AppCollab AC-P3 设计系统协作分身绑定真实 PG 测试（零 mock）。

覆盖 AC-P3a：
- 分身 save 一套新设计系统 → bound_agent_id 自动绑该分身（创建即绑，与 deck DECKBIND 同模型）；
- bind-only-if-unbound：已绑定不因 owner 再 save / 其它 save 静默改绑；
- owner set_bound_agent 改绑 / 解绑（None）；
- 非 owner 改绑被拒（绑定是 owner 概念）。

直接打真实本地 PostgreSQL（端口 15432）；不可达则 skip。uuid tag 隔离测试行。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn_designsystem.service.design_system_service import Subject, design_system_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
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


def _content() -> dict:
    return {
        'tokens_css': ':root { --bg: #101010; }',
        'design_tokens_json': {'schemaVersion': 1, 'tokens': []},
        'tailwind_css': '@theme {}',
        'design_md': '# 设计说明',
        'components_html': '<button>Go</button>',
        'components_manifest_json': {'groups': []},
        'token_contract_report_json': {'summary': {'score': 80, 'grade': 'good', 'recommendRebuild': False}},
    }


async def _seed_identities(session, owner: str, *agents: str, extra_owners: tuple[str, ...] = ()) -> None:
    """提交真实主人/分身身份，使独立 IM 事务能够执行严格身份校验。"""
    identities: list[HasnHumans | HasnAgents] = []
    for index, human_id in enumerate((owner, *extra_owners)):
        user_id = 1_500_000_000 + int(uuid.uuid4().int % 400_000_000)
        identities.append(
            HasnHumans(
                hasn_id=human_id,
                star_id=f's_{uuid.uuid4().hex[:12]}_{index}',
                user_id=user_id,
                nickname=f'设计系统主人 {human_id[-8:]} {index + 1}',
                status='active',
            )
        )
    for index, agent_id in enumerate(agents):
        identities.append(
            HasnAgents(
                hasn_id=agent_id,
                star_id=f'sa_{uuid.uuid4().hex[:12]}_{index}',
                owner_id=owner,
                display_name=f'设计系统分身 {index + 1}',
                agent_name=f'ds_{uuid.uuid4().hex[:8]}',
                status='active',
            )
        )
    session.add_all(identities)
    await session.commit()


async def test_agent_save_binds_generating_agent(session) -> None:
    """分身 save 一套新设计系统 → bound_agent_id 自动 = 该分身（创建即绑）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    agent = f'a_ds_{tag}'
    await _seed_identities(session, owner, agent)
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'ba-{tag}',
        name='分身生成',
        content=_content(),
    )
    assert saved['owner_hasn_id'] == owner
    assert saved['bound_agent_id'] == agent  # 创建即绑生成它的分身


async def test_owner_save_does_not_bind(session) -> None:
    """owner 本人 save → 不绑（无分身可绑），bound_agent_id 保持 None。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    await _seed_identities(session, owner)
    saved = await design_system_service.save(
        session,
        subject=Subject.human(owner),
        design_system_id=None,
        slug=f'ba-{tag}',
        name='owner 建',
        content=_content(),
    )
    assert saved['bound_agent_id'] is None


async def test_bind_only_if_unbound_keeps_first_agent(session) -> None:
    """已绑定后 owner 再 save 不静默改绑——bound 仍是首个生成分身（bind-only-if-unbound）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    agent = f'a_ds_{tag}'
    await _seed_identities(session, owner, agent)
    first = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'ba-{tag}',
        name='分身首版',
        content=_content(),
    )
    ds_id = first['id']
    assert first['bound_agent_id'] == agent

    # owner 本人再 save 同一套（同 design_system_id）→ 不改绑。
    again = await design_system_service.save(
        session,
        subject=Subject.human(owner),
        design_system_id=ds_id,
        slug=f'ba-{tag}',
        name='owner 改名',
        content=_content(),
    )
    assert again['bound_agent_id'] == agent  # 仍是首个分身，未被 owner save 抹掉


async def test_owner_rebind_and_unbind(session) -> None:
    """owner set_bound_agent 改绑到另一分身 → 生效；解绑(None) → bound 置空。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    agent1 = f'a_ds1_{tag}'
    agent2 = f'a_ds2_{tag}'
    await _seed_identities(session, owner, agent1, agent2)
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent1, owner),
        design_system_id=None,
        slug=f'ba-{tag}',
        name='待改绑',
        content=_content(),
    )
    ds_id = saved['id']
    assert saved['bound_agent_id'] == agent1

    rebound = await design_system_service.set_bound_agent(
        session, owner_hasn_id=owner, design_system_id=ds_id, bound_agent_id=agent2
    )
    assert rebound['bound_agent_id'] == agent2

    unbound = await design_system_service.set_bound_agent(
        session, owner_hasn_id=owner, design_system_id=ds_id, bound_agent_id=None
    )
    assert unbound['bound_agent_id'] is None


async def test_non_owner_cannot_rebind(session) -> None:
    """非 owner 调 set_bound_agent → ForbiddenError（绑定是 owner 概念）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    other = f'h_other_{tag}'
    agent = f'a_ds_{tag}'
    await _seed_identities(session, owner, agent, extra_owners=(other,))
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'ba-{tag}',
        name='他人不可改绑',
        content=_content(),
    )
    with pytest.raises(errors.ForbiddenError):
        await design_system_service.set_bound_agent(
            session, owner_hasn_id=other, design_system_id=saved['id'], bound_agent_id=f'a_evil_{tag}'
        )

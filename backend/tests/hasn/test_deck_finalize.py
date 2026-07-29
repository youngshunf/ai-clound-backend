"""deck.finalize：显式收尾工具真实 PG 测试（零 mock）。

分身写完最后一页调 hasn.deck.finalize → 状态 draft/generating→ready（幂等，仅首次 changed=True）；
首次收尾服务端自动给主人发一张「演示文稿做好了」卡片（content_type=5，深链用云端权威 deck id）；
重复 finalize / 重复 emit（同 local_id）不重复发卡。

分身对主人名下 deck 的权限：分身继承主人权限（deck.owner_id == agent.owner_hasn_id → manager）。
"""
from __future__ import annotations

import uuid
import time

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.model.hasn_messages import HasnMessages
from backend.app.hasn.service.hasn_sessions_service import emit_deck_completion_card
from backend.app.hasn.service.session_im_outbox import build_session_im_relay
from backend.app.hasn_deck.service.deck_service import Subject, deck_service
from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:10]


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


@pytest_asyncio.fixture
async def seeded(session):
    tag = _uid()
    uid_owner = 1_200_000_000 + int(uuid.uuid4().int % 800_000_000)
    owner = f'h_own_{tag}'
    agent = f'a_mine_{tag}'
    session.add_all(
        [
            HasnHumans(
                hasn_id=owner, star_id=f's_{uid_owner}', user_id=uid_owner, nickname=f'主人{tag}', status='active'
            ),
            HasnAgents(
                hasn_id=agent,
                star_id=f'sa_{tag}',
                owner_id=owner,
                display_name=f'我的分身{tag}',
                agent_name=f'mine{tag}',
                status='active',
            ),
        ]
    )
    await session.commit()
    return {'session': session, 'owner': owner, 'agent': agent}


async def test_finalize_flips_ready_and_idempotent(seeded) -> None:
    """分身收尾主人 deck：首次 draft→ready(changed=True)，再调 changed=False 且仍 ready。"""
    session, owner, agent = seeded['session'], seeded['owner'], seeded['agent']
    deck = await deck_service.create_deck(session, owner_id=owner, title='收尾测试')
    deck_id = deck['id']
    assert deck['status'] in ('draft', 'generating')

    subj = Subject.agent(agent, owner_hasn_id=owner)  # 分身继承主人权限 → manager
    first = await deck_service.finalize_deck(session, subject=subj, deck_id=deck_id)
    assert first['changed'] is True
    assert first['status'] == 'ready'

    again = await deck_service.finalize_deck(session, subject=subj, deck_id=deck_id)
    assert again['changed'] is False
    assert again['status'] == 'ready'


async def test_finalize_emits_completion_card_once(seeded) -> None:
    """deck 完成卡先与业务同事务入 outbox，再由真实 relay 幂等落入主会话。"""
    session, owner, agent = seeded['session'], seeded['owner'], seeded['agent']
    deck = await deck_service.create_deck(session, owner_id=owner, title='季度汇报')
    deck_id = deck['id']
    session_factory = async_sessionmaker(session.bind, expire_on_commit=False)
    gateway = PythonLocalImGateway(session_factory)

    delivery = await emit_deck_completion_card(
        session,
        owner_id=owner,
        agent_id=agent,
        deck_id=str(deck_id),
        title='季度汇报',
        summary='第一版做好了',
        im_gateway=gateway,
    )
    assert delivery['status'] == 'pending'

    def _cards():
        stmt = select(HasnMessages).where(
            HasnMessages.from_id == agent,
            HasnMessages.to_id == owner,
            HasnMessages.content_type == 5,
        )
        return session.execute(stmt)

    # relay 前没有权威消息；只持久化生产方命令。
    rows = (await _cards()).scalars().all()
    assert rows == []
    await session.commit()
    relay = build_session_im_relay(
        session_factory=session_factory,
        gateway=gateway,
        instance_id=f'deck-test-{uuid.uuid4().hex}',
    )
    stats = await relay.drain_once(now=int(time.time()) + 2)
    assert stats.completed >= 1

    rows = (await _cards()).scalars().all()
    assert len(rows) == 1, f'应恰好落 1 张完成卡，实际 {len(rows)}'
    card = rows[0].content
    assert card['title'] == '演示文稿做好了'
    assert card['source']['id'] == 'deck'
    assert card['resource']['uri'] == f'hasn://deck/{deck_id}'
    assert card['primary_action']['uri'] == f'hasn://deck/{deck_id}'
    assert card['description'] == '第一版做好了'

    # 幂等：同 deck_id → 同 local_id(deck_complete:{id}) → 不重复发卡
    duplicate = await emit_deck_completion_card(
        session,
        owner_id=owner,
        agent_id=agent,
        deck_id=str(deck_id),
        title='季度汇报',
        summary='第一版做好了',
        im_gateway=gateway,
    )
    assert duplicate['command_id'] == delivery['command_id']
    await session.commit()
    await relay.drain_once(now=int(time.time()) + 2)
    rows2 = (await _cards()).scalars().all()
    assert len(rows2) == 1, f'重复 emit 应幂等不重复发卡，实际 {len(rows2)}'

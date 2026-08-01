"""分身回复进度瞬态转发的真实 PostgreSQL 判决测试。

钉死三条判决（转发目标 = 会话受众 owner 集合 − 发送分身的主人）：
- 跨主人 1:1：只转给对端主人，发送分身自己的主人不重复收（它本地已有完整流式过程）；
- 非参与者伪造帧：直接丢弃；
- 会话不存在 / id 非法：丢弃且不抛异常。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_im.application import agent_progress_service
from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.app.hasn_im.ports.dto import (
    ActorKind,
    EnsureDirectConversationCommand,
    ServicePrincipal,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL


pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def relay_sessions():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:
        await engine.dispose()
        pytest.fail(f'PostgreSQL 不可达：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _human(hasn_id: str, marker: str, nickname: str) -> HasnHumans:
    # 昵称有全局唯一约束，测试数据一律带 marker 后缀（测试数据保留不删，见 CLAUDE.md）。
    return HasnHumans(
        hasn_id=hasn_id,
        star_id=f'h{marker[:24]}',
        user_id=int(marker[:15], 16),
        nickname=f'{nickname}{marker[:8]}',
        status='active',
    )


def _agent(hasn_id: str, owner_id: str, marker: str, name: str) -> HasnAgents:
    return HasnAgents(
        hasn_id=hasn_id,
        star_id=f'a{marker[:24]}',
        owner_id=owner_id,
        display_name=name,
        agent_name=f'prog{marker[:12]}',
        api_key_hash=marker,
        status='active',
        created_via='client',
    )


def _progress_frame(from_id: str, conversation_id: str) -> dict:
    return {
        'hasn': 'hasn/0.2',
        'method': 'hasn.agent.progress',
        'params': {
            'from_id': from_id,
            'conversation_id': conversation_id,
            'phase': 'update',
            'tool_count': 3,
            'elapsed_ms': 12000,
            'seq': 7,
        },
    }


async def test_cross_owner_progress_goes_to_peer_owner_only(relay_sessions) -> None:
    """跨主人 1:1：进度只转给对端主人；分身自己的主人不在转发目标里。"""
    marker = uuid.uuid4().hex
    peer_marker = uuid.uuid4().hex
    agent_owner = f'h_prga_{marker[:18]}'
    agent_id = f'a_prg_{marker[:19]}'
    peer_owner = f'h_prgb_{peer_marker[:18]}'

    async with relay_sessions.begin() as db:
        db.add(_human(agent_owner, marker, '进度分身的主人'))
        db.add(_human(peer_owner, peer_marker, '对端主人'))
        db.add(_agent(agent_id, agent_owner, marker, '进度分身'))

    gateway = PythonLocalImGateway(session_factory=relay_sessions)
    principal = ServicePrincipal(
        canonical_sender=agent_id,
        actor_kind=ActorKind.AGENT,
        origin_node_id='node-progress-pg',
    )
    reference = await gateway.ensure_direct_conversation(
        EnsureDirectConversationCommand(peer_hasn_id=peer_owner),
        principal,
    )
    conversation_id = reference.conversation_id

    async with relay_sessions() as db:
        targets = await agent_progress_service.relay_agent_progress(
            db,
            from_id=agent_id,
            conversation_id=conversation_id,
            payload=_progress_frame(agent_id, conversation_id),
        )

    assert targets == [peer_owner]


async def test_progress_from_non_participant_is_dropped(relay_sessions) -> None:
    """伪造帧：分身不是该会话参与者 → 零转发目标。"""
    marker = uuid.uuid4().hex
    peer_marker = uuid.uuid4().hex
    outsider_marker = uuid.uuid4().hex
    agent_owner = f'h_prgc_{marker[:18]}'
    agent_id = f'a_prgc_{marker[:18]}'
    peer_owner = f'h_prgd_{peer_marker[:18]}'
    outsider_agent = f'a_prgx_{outsider_marker[:18]}'

    async with relay_sessions.begin() as db:
        db.add(_human(agent_owner, marker, '进度分身的主人'))
        db.add(_human(peer_owner, peer_marker, '对端主人'))
        db.add(_agent(agent_id, agent_owner, marker, '进度分身'))
        db.add(_agent(outsider_agent, agent_owner, outsider_marker, '会话外分身'))

    gateway = PythonLocalImGateway(session_factory=relay_sessions)
    reference = await gateway.ensure_direct_conversation(
        EnsureDirectConversationCommand(peer_hasn_id=peer_owner),
        ServicePrincipal(
            canonical_sender=agent_id,
            actor_kind=ActorKind.AGENT,
            origin_node_id='node-progress-pg',
        ),
    )
    conversation_id = reference.conversation_id

    async with relay_sessions() as db:
        targets = await agent_progress_service.relay_agent_progress(
            db,
            from_id=outsider_agent,
            conversation_id=conversation_id,
            payload=_progress_frame(outsider_agent, conversation_id),
        )

    assert targets == []


@pytest.mark.parametrize('bad_conversation_id', (str(uuid.uuid4()), 'not-a-uuid'))
async def test_unknown_conversation_is_dropped_without_raising(
    relay_sessions,
    bad_conversation_id: str,
) -> None:
    """会话不存在或 id 非法：丢弃，绝不冒泡异常（瞬态帧不能炸收发循环）。"""
    async with relay_sessions() as db:
        targets = await agent_progress_service.relay_agent_progress(
            db,
            from_id='a_never_exists',
            conversation_id=bad_conversation_id,
            payload=_progress_frame('a_never_exists', bad_conversation_id),
        )
    assert targets == []

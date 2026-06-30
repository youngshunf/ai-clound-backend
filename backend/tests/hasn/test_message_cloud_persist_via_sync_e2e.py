"""doc16 Phase A「消息上云」sink 进程内 E2E（真实 PG，零 mock）。

验证 owner↔自有分身的会话消息经既有 ``/sync/push`` 路径（``save_client_event`` /
``FEED_MESSAGE_EVENT_TYPES``）**额外落入权威 ``hasn_messages``**（单一云端记忆提取的数据
源），同时不破坏既有 feed（``hasn_sync_events``）跨设备同步、不重复写 feed。

覆盖：
- outbound（message.sent，主人→分身）+ inbound（message.agent_reply，分身→主人）都在
  ``hasn_messages`` 落出权威行，方向/内容/时序保真，复用同一 loopback 会话。
- inbound 回复 payload 无 ``local_id`` → 以 daemon 本地 ``message_id`` 作幂等键落库。
- 跨节点重推同一消息 → ``hasn_messages`` 不产生第二行（local_id 幂等），feed 也只一条
  （sink 不重复写 feed）。
- 非本主人名下分身的消息事件 → sink 诚实跳过、绝不连累 feed（best-effort 边界）。

事务末尾回滚，不污染库。需要 export DATABASE_PORT=15432（指向本地开发 PG）。
设计：docs/hasn-node设计文档/02-记忆与知识库/16-记忆系统云端权威重构（消息上云+工作会话同步+单一提取）.md
"""
from __future__ import annotations

import uuid

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import HasnConversations, HasnMessages
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.schema.hasn_sync import ClientEvent
from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway
from backend.database.db import SQLALCHEMY_DATABASE_URL

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:10]


@pytest_asyncio.fixture
async def seeded() -> AsyncIterator[dict]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    uid_owner = 980000 + int(uuid.uuid4().int % 9000)
    uid_stranger = uid_owner + 1
    owner = f'h_own_{_uid()}'
    stranger = f'h_str_{_uid()}'
    my_agent = f'a_mine_{_uid()}'
    others_agent = f'a_other_{_uid()}'
    session.add_all([
        HasnHumans(hasn_id=owner, star_id=f's_{uid_owner}', user_id=uid_owner, nickname='Owner', status='active'),
        HasnHumans(
            hasn_id=stranger, star_id=f's_{uid_stranger}', user_id=uid_stranger, nickname='Stranger', status='active'
        ),
        HasnAgents(
            hasn_id=my_agent, star_id=f'sa_{_uid()}', owner_id=owner,
            display_name='我的分身', agent_name='mine', status='active',
        ),
        HasnAgents(
            hasn_id=others_agent, star_id=f'sa_{_uid()}', owner_id=stranger,
            display_name='别人的分身', agent_name='other', status='active',
        ),
    ])
    await session.flush()
    try:
        yield {'session': session, 'owner': owner, 'my_agent': my_agent, 'others_agent': others_agent}
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


def _msg_sent_event(owner_id: str, agent_id: str, message_id: str, local_id: str | None) -> ClientEvent:
    payload = {
        'message_id': message_id,
        'conversation_id': f'conv_local_{_uid()}',
        'owner_id': owner_id,
        'hasn_id': owner_id,
        'sender_hasn_id': owner_id,
        'recipient_hasn_id': agent_id,
        'kind': 'direct',
        'peer_hasn_id': agent_id,
        'direction': 'outbound',
        'content_type': 'text',
        'content_body': {'text': '帮我把这周复盘整理一下'},
        'source': 'local_user',
        'created_at': 1780000000,
    }
    if local_id is not None:
        payload['local_id'] = local_id
    return ClientEvent(
        client_event_id=f'evt_msg_sent_{message_id}',
        event_type='message.sent',
        hasn_id=owner_id,
        dedupe_key=message_id,
        payload=payload,
    )


def _agent_reply_event(owner_id: str, agent_id: str, message_id: str) -> ClientEvent:
    # 分身回复镜像：local_id 缺失（daemon 端 reply 不带 client local_id），content_type
    # 用 'text/plain'（真实 reply 形态），sink 须以 message_id 回退作幂等键。
    return ClientEvent(
        client_event_id=f'evt_msg_agent_reply_{message_id}',
        event_type='message.agent_reply',
        hasn_id=owner_id,
        dedupe_key=message_id,
        payload={
            'message_id': message_id,
            'conversation_id': f'conv_local_{_uid()}',
            'owner_id': owner_id,
            'hasn_id': owner_id,
            'sender_hasn_id': agent_id,
            'recipient_hasn_id': owner_id,
            'kind': 'direct',
            'peer_hasn_id': agent_id,
            'direction': 'inbound',
            'content_type': 'text/plain',
            'content_body': {'text': '好的，已整理完毕'},
            'source': 'runtime',
            'created_at': 1780000005,
        },
    )


async def _count_messages(session: AsyncSession, local_id: str) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(HasnMessages).where(HasnMessages.local_id == local_id)
        )
    ).scalar_one()


async def _count_feed(session: AsyncSession, owner_id: str, message_id: str, event_type: str) -> int:
    from backend.app.hasn.model.hasn_sync_events import HasnSyncEvents

    return (
        await session.execute(
            select(func.count())
            .select_from(HasnSyncEvents)
            .where(
                HasnSyncEvents.owner_id == owner_id,
                HasnSyncEvents.aggregate_type == 'message',
                HasnSyncEvents.aggregate_id == str(message_id),
                HasnSyncEvents.event_type == event_type,
            )
        )
    ).scalar_one()


async def test_loopback_messages_land_in_authoritative_hasn_messages(seeded: dict) -> None:
    session = seeded['session']
    owner, my_agent = seeded['owner'], seeded['my_agent']
    gw = SqlAlchemySyncGateway()
    node = 'node_dev_1'

    sent_id = f'msg_send_{_uid()}'
    out_local = f'lid_out_{_uid()}'
    reply_id = f'msg_reply_{_uid()}'

    # 1) outbound（主人→分身）经 sync sink → 落 hasn_messages（权威）
    rev_sent = await gw.save_client_event(
        session, owner_id=owner, node_id=node, event=_msg_sent_event(owner, my_agent, sent_id, out_local)
    )
    assert rev_sent is not None and rev_sent >= 1  # feed 仍写（跨设备不破坏）
    await session.flush()

    out_row = (
        await session.execute(select(HasnMessages).where(HasnMessages.local_id == out_local))
    ).scalar_one()
    assert out_row.from_id == owner and out_row.to_id == my_agent
    assert out_row.content == {'text': '帮我把这周复盘整理一下'}
    assert int(out_row.created_time.timestamp()) == 1780000000  # 客户端时序保真
    conv_id = str(out_row.conversation_id)

    # 会话经 CONV-C1 原子 get_or_create：参与者排序后唯一
    conv = await session.get(HasnConversations, conv_id)
    assert conv is not None
    assert {conv.participant_a_id, conv.participant_b_id} == {owner, my_agent}

    # sink 不重复写 feed：message.sent feed 事件恰一条
    assert await _count_feed(session, owner, sent_id, 'message.sent') == 1

    # 2) inbound（分身→主人回复，无 local_id）→ message_id 作幂等键落库，复用同一会话
    rev_reply = await gw.save_client_event(
        session, owner_id=owner, node_id=node, event=_agent_reply_event(owner, my_agent, reply_id)
    )
    assert rev_reply is not None and rev_reply > rev_sent
    await session.flush()
    in_row = (
        await session.execute(select(HasnMessages).where(HasnMessages.local_id == reply_id))
    ).scalar_one()
    assert in_row.from_id == my_agent and in_row.to_id == owner
    assert in_row.content == {'text': '好的，已整理完毕'}
    assert str(in_row.conversation_id) == conv_id  # 同一 loopback 会话


async def test_cross_node_repush_no_duplicate_message_row(seeded: dict) -> None:
    session = seeded['session']
    owner, my_agent = seeded['owner'], seeded['my_agent']
    gw = SqlAlchemySyncGateway()

    msg_id = f'msg_send_{_uid()}'
    out_local = f'lid_out_{_uid()}'

    await gw.save_client_event(
        session, owner_id=owner, node_id='node_A', event=_msg_sent_event(owner, my_agent, msg_id, out_local)
    )
    # 换 node 重推同一 client_event：inbox 去重不命中、save_client_event 继续；feed 幂等兜住，
    # hasn_messages 由 local_id 幂等兜住 → 不产生第二行。
    await gw.save_client_event(
        session, owner_id=owner, node_id='node_B', event=_msg_sent_event(owner, my_agent, msg_id, out_local)
    )
    await session.flush()
    assert await _count_messages(session, out_local) == 1, 'local_id 幂等失败：重推产生重复 hasn_messages 行'
    assert await _count_feed(session, owner, msg_id, 'message.sent') == 1, 'feed 出现重复消息'


async def test_others_agent_message_skipped_does_not_break_feed(seeded: dict) -> None:
    # 非本主人名下分身的消息事件：sink best-effort 跳过（不落 hasn_messages），但 feed 仍写
    # （跨设备同步绝不被 sink 连累）。这正是「最坏只是少喂一条提取信号」的安全边界。
    session = seeded['session']
    owner, others_agent = seeded['owner'], seeded['others_agent']
    gw = SqlAlchemySyncGateway()

    msg_id = f'msg_send_{_uid()}'
    out_local = f'lid_out_{_uid()}'
    rev = await gw.save_client_event(
        session, owner_id=owner, node_id='node_A', event=_msg_sent_event(owner, others_agent, msg_id, out_local)
    )
    await session.flush()
    assert rev is not None and rev >= 1  # feed 照常写
    assert await _count_feed(session, owner, msg_id, 'message.sent') == 1
    assert await _count_messages(session, out_local) == 0, '越权分身消息不应落 hasn_messages'

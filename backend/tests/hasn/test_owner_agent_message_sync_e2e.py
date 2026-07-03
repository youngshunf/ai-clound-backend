"""owner↔分身会话消息上 feed 进程内 E2E（实施/91 C-T，真实 PG，零 mock）。

直接驱动 SqlAlchemySyncGateway.save_client_event（message.sent / message.agent_reply）
与 pull_events，断言：
  1) message.sent（主人发给自己分身）与 message.agent_reply（分身回复）都落入权威 feed
     hasn_sync_events，sync/pull 能完整拉回，content_body 原样保留。
  2) 跨节点重复 push 同一 message_id → feed 不产生第二条（幂等去重，防换设备双份）。
  3) task.* 不串入消息 feed（pull_events 本就排除 task.%；这里间接确认 message 分支不误伤）。

事务末尾回滚，不污染库。需要 export DATABASE_PORT=15432（指向本地开发 PG）。

设计：docs/hasn-node设计文档/02-数据与同步/06-owner与分身会话跨设备同步修复设计.md
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.schema.hasn_sync import ClientEvent
from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _owner() -> str:
    # 全新 owner_id：feed revision 从 1 起，天然隔离；末尾回滚兜底。
    return f'h_{uuid.uuid4()}'


def _msg_sent_event(owner_id: str, agent_id: str, conv_id: str, message_id: str) -> ClientEvent:
    return ClientEvent(
        client_event_id=f'evt_msg_sent_{message_id}',
        event_type='message.sent',
        hasn_id=owner_id,
        dedupe_key=message_id,
        payload={
            'message_id': message_id,
            'conversation_id': conv_id,
            'owner_id': owner_id,
            'hasn_id': owner_id,
            'sender_hasn_id': owner_id,
            'recipient_hasn_id': agent_id,
            'kind': 'direct',
            'relation_view': 'agent_copy',
            'peer_hasn_id': agent_id,
            'direction': 'outbound',
            'content_type': 'text',
            'content_body': {'text': '你好分身'},
            'source': 'local_user',
            'created_at': 1780000000,
        },
    )


def _agent_reply_event(owner_id: str, agent_id: str, conv_id: str, message_id: str) -> ClientEvent:
    return ClientEvent(
        client_event_id=f'evt_msg_agent_reply_{message_id}',
        event_type='message.agent_reply',
        hasn_id=owner_id,
        dedupe_key=message_id,
        payload={
            'message_id': message_id,
            'conversation_id': conv_id,
            'owner_id': owner_id,
            'hasn_id': owner_id,
            'sender_hasn_id': agent_id,
            'recipient_hasn_id': owner_id,
            'kind': 'direct',
            'relation_view': 'agent_copy',
            'peer_hasn_id': agent_id,
            'direction': 'inbound',
            'content_type': 'text',
            'content_body': {'text': '主人你好，我在'},
            'source': 'runtime',
            'created_at': 1780000005,
        },
    )


async def test_owner_agent_messages_land_in_feed_and_pull_back(db_session) -> None:
    gw = SqlAlchemySyncGateway()
    owner_id = _owner()
    agent_id = f'a_{uuid.uuid4()}'
    conv_id = str(uuid.uuid4())
    node = 'node_dev_1'

    sent_id = f'msg_send_{uuid.uuid4().hex[:12]}'
    reply_id = f'msg_reply_{uuid.uuid4().hex[:12]}'

    rev_sent = await gw.save_client_event(
        db_session, owner_id=owner_id, node_id=node,
        event=_msg_sent_event(owner_id, agent_id, conv_id, sent_id),
    )
    rev_reply = await gw.save_client_event(
        db_session, owner_id=owner_id, node_id=node,
        event=_agent_reply_event(owner_id, agent_id, conv_id, reply_id),
    )
    # 两条都拿到 server_revision（落进了 feed，不再是 None 死信）。
    assert rev_sent is not None and rev_sent >= 1
    assert rev_reply is not None and rev_reply > rev_sent

    events = await gw.pull_events(db_session, owner_id=owner_id, after_revision=0, limit=100)
    by_type = {e.event_type: e for e in events}
    assert 'message.sent' in by_type, '主人提问未进 feed'
    assert 'message.agent_reply' in by_type, '分身回复未进 feed'
    assert by_type['message.sent'].payload['content_body']['text'] == '你好分身'
    assert by_type['message.agent_reply'].payload['content_body']['text'] == '主人你好，我在'
    assert by_type['message.agent_reply'].payload['relation_view'] == 'agent_copy'
    assert by_type['message.agent_reply'].payload['peer_hasn_id'] == agent_id


async def test_cross_node_repush_is_idempotent_no_duplicate(db_session) -> None:
    gw = SqlAlchemySyncGateway()
    owner_id = _owner()
    agent_id = f'a_{uuid.uuid4()}'
    conv_id = str(uuid.uuid4())
    msg_id = f'msg_send_{uuid.uuid4().hex[:12]}'

    rev_first = await gw.save_client_event(
        db_session, owner_id=owner_id, node_id='node_A',
        event=_msg_sent_event(owner_id, agent_id, conv_id, msg_id),
    )
    # 换一个 node 重推同一 message_id：inbox 层 (owner,node,client_event_id) 去重不命中，
    # 必须由 feed 层 (owner,aggregate_id,event_type) 幂等兜住，不产生第二条 feed。
    rev_second = await gw.save_client_event(
        db_session, owner_id=owner_id, node_id='node_B',
        event=_msg_sent_event(owner_id, agent_id, conv_id, msg_id),
    )
    assert rev_first is not None
    assert rev_second == rev_first, 'feed 幂等失败：跨节点重推应返回既有 revision'

    events = await gw.pull_events(db_session, owner_id=owner_id, after_revision=0, limit=100)
    sent_events = [e for e in events if e.event_type == 'message.sent']
    assert len(sent_events) == 1, f'feed 出现重复消息：{len(sent_events)} 条'

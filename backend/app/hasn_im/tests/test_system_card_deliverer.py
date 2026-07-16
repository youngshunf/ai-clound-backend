"""R1-06 验收：`deliver_system_card` 逐字节复刻现网 `_persist_card` 旁路（真实 PG，零 mock）。

R1-06 把 notification 域的第二套落库旁路（`notification_carrier._persist_card` 直连
`persist_message` + 手动 `_append_sync_event`）收编进通信域 `hasn_im.application.
system_card_deliverer.deliver_system_card`。收编只搬家不改语义，故本组用真 PG
（DATABASE_PORT=15432）驱动 deliverer 本体，钉三件事与现网**逐字节一致**：

1. **卡片落库**：落一条 `content_type=5`、`msg_type` 指定、from/to 正确的消息到「from ⇄
   recipient」会话（get_or_create 建 relation_type 指定的会话）；
2. **同步事件**：写一条 `message.received` sync event（owner=recipient、aggregate=msg_id），
   使接收方节点经 sync/pull 镜像这条卡片（persist_message 直写不产生同步事件，缺这步
   云端落库但 daemon 永不镜像——这正是 `_persist_card` 手动补事件的原因）；
3. **会话幂等**：同一 from⇄recipient 再投一次 → 复用同一会话（get_or_create 幂等），
   落第二条消息、第二条事件。

PG 不可达时跳过而非硬失败；每个用例用 uuid 派生全新参与者，末尾清理自身行，不污染库。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import HasnConversations, HasnMessages
from backend.app.hasn_im.application.system_card_deliverer import deliver_system_card
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def sessionmaker_pg():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool, future=True)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过系统卡片投递契约测试：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _fresh_pair() -> tuple[str, str]:
    """(服务号 from_id, 主人 recipient_id)——服务号非 h_/a_ 前缀（sa_），recipient 恒 h_ 主人。"""
    return f'sa_sc{uuid.uuid4().hex[:16]}', f'h_sc{uuid.uuid4().hex[:16]}'


def _sample_card_body(title: str) -> dict:
    """最小合法卡片体（本组只验投递机制，不经 schema 校验——那是 build_card_body 的职责）。"""
    return {'schema_version': 'hasn.card/0.1', 'title': title, 'description': None}


async def _cleanup(sessionmaker, from_id: str, recipient_id: str) -> None:
    async with sessionmaker() as session:
        ids = (
            (
                await session.execute(
                    sa.text(
                        'SELECT id FROM public.hasn_conversations '
                        'WHERE participant_a_id IN (:a, :b) OR participant_b_id IN (:a, :b)'
                    ),
                    {'a': from_id, 'b': recipient_id},
                )
            )
            .scalars()
            .all()
        )
        if ids:
            msg_ids = (
                (
                    await session.execute(
                        sa.text('SELECT id FROM public.hasn_messages WHERE conversation_id = ANY(:ids)'),
                        {'ids': [str(i) for i in ids]},
                    )
                )
                .scalars()
                .all()
            )
            if msg_ids:
                await session.execute(
                    sa.text(
                        'DELETE FROM public.hasn_sync_events '
                        "WHERE aggregate_type = 'message' AND aggregate_id = ANY(:mids)"
                    ),
                    {'mids': [str(m) for m in msg_ids]},
                )
            await session.execute(
                sa.text('DELETE FROM public.hasn_messages WHERE conversation_id = ANY(:ids)'),
                {'ids': [str(i) for i in ids]},
            )
            await session.execute(
                sa.text('DELETE FROM public.hasn_conversations WHERE id = ANY(:ids)'),
                {'ids': [str(i) for i in ids]},
            )
            await session.commit()


async def test_deliver_system_card_lands_card_and_sync_event(sessionmaker_pg) -> None:
    """投递一张系统卡片 → 落 content_type=5 卡片 + message.received sync event（owner=recipient）。"""
    from_id, recipient_id = _fresh_pair()
    card = _sample_card_body('新通知')
    try:
        async with sessionmaker_pg() as session:
            msg_id = await deliver_system_card(
                session,
                recipient_id=recipient_id,
                recipient_type='human',
                from_id=from_id,
                peer_type='service',
                relation_type='service',
                conversation_type='service',
                card_body=card,
                priority='normal',
                msg_type='notification',
                notif_id=123,
            )
            await session.commit()

        assert isinstance(msg_id, int) and msg_id > 0, 'deliver 应返回落库消息 id'

        async with sessionmaker_pg() as session:
            msg = await session.get(HasnMessages, msg_id)
            assert msg is not None, '卡片消息必须落库'
            assert msg.content_type == 5, '卡片 content_type 恒 5'
            assert msg.from_id == from_id
            assert msg.to_id == recipient_id
            assert msg.msg_type == 'notification'

            conv = await session.get(HasnConversations, str(msg.conversation_id))
            assert conv is not None, 'get_or_create 必须建/命中会话'
            # relation_type='service' → 落服务号会话（与现网 _persist_card 同口径）
            assert conv.relation_type == 'service'

            # message.received sync event：owner=recipient、aggregate=msg_id（缺它 daemon 永不镜像）
            evt_count = (
                await session.execute(
                    sa.text(
                        'SELECT count(*) FROM public.hasn_sync_events '
                        "WHERE event_type = 'message.received' "
                        'AND aggregate_id = :mid AND owner_id = :owner'
                    ),
                    {'mid': str(msg_id), 'owner': recipient_id},
                )
            ).scalar()
            assert evt_count == 1, 'message.received sync event 必须恰有一条（owner=recipient）'
    finally:
        await _cleanup(sessionmaker_pg, from_id, recipient_id)


async def test_deliver_reuses_conversation_on_second_card(sessionmaker_pg) -> None:
    """同一 from⇄recipient 再投一张 → 复用同一会话（get_or_create 幂等），两条消息两条事件。"""
    from_id, recipient_id = _fresh_pair()
    try:
        async with sessionmaker_pg() as session:
            first_id = await deliver_system_card(
                session,
                recipient_id=recipient_id,
                recipient_type='human',
                from_id=from_id,
                peer_type='service',
                relation_type='service',
                conversation_type='service',
                card_body=_sample_card_body('第一条'),
                priority='high',
            )
            await session.commit()
        async with sessionmaker_pg() as session:
            second_id = await deliver_system_card(
                session,
                recipient_id=recipient_id,
                recipient_type='human',
                from_id=from_id,
                peer_type='service',
                relation_type='service',
                conversation_type='service',
                card_body=_sample_card_body('第二条'),
                priority='normal',
            )
            await session.commit()

        assert first_id != second_id, '两次投递落两条不同消息'
        async with sessionmaker_pg() as session:
            m1 = await session.get(HasnMessages, first_id)
            m2 = await session.get(HasnMessages, second_id)
            assert str(m1.conversation_id) == str(m2.conversation_id), 'get_or_create 幂等：复用同一会话'
            evt_count = (
                await session.execute(
                    sa.text(
                        'SELECT count(*) FROM public.hasn_sync_events '
                        "WHERE event_type = 'message.received' AND owner_id = :owner "
                        'AND aggregate_id = ANY(:mids)'
                    ),
                    {'owner': recipient_id, 'mids': [str(first_id), str(second_id)]},
                )
            ).scalar()
            assert evt_count == 2, '两条卡片各自一条 message.received 事件'
    finally:
        await _cleanup(sessionmaker_pg, from_id, recipient_id)

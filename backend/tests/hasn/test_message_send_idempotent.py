"""出站投递幂等去重回归（真实 PG，零 mock）。

背景（MSGSEND 可靠投递方案2 的云端前置 C1）：daemon 的出站投递队列在 `/ws/node`
断连/重连后会**重发**同一帧（带相同客户端 `local_id`），用于补达「发出去却丢在断连
窗口」的 H2H 消息。云端 `route_message` 必须对已落库的 `local_id` 幂等——

1. **命中既有行直接回原 msg_id+conversation_id**（status=sent, deduped=True），
   **不二次落库、不二次投递**：发送端据此补到 ack 标记已达、停止重发，对端绝不会
   收到重复消息。
2. 幂等判定在 route_message 最顶部（step 0，先于目标解析/关系/权限校验），故重发
   即便此刻关系/权限已变也仍能补到 ack（已落库即已投递过，语义正确）。

需要本地开发 PG（export DATABASE_PORT=15432）；PG 不可达时跳过而非硬失败。
每个用例用 uuid 派生全新参与者，末尾清理自身行，不污染库。
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
from backend.app.hasn.service.message_router import route_message
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
        pytest.skip(f'PostgreSQL 不可达，跳过出站幂等回归：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _fresh_pair() -> tuple[str, str]:
    a, b = sorted([f'h_mi{uuid.uuid4().hex[:18]}', f'h_mi{uuid.uuid4().hex[:18]}'])
    return a, b


async def _seed_conversation_with_message(
    sessionmaker, a_id: str, b_id: str, local_id: str
) -> tuple[str, int]:
    """直接播一条 direct 会话 + 一条带 local_id 的已落库消息，返回 (conv_id, msg_id)。"""
    async with sessionmaker() as session:
        conv = HasnConversations(
            type='direct',
            relation_type='social',
            participant_a_id=a_id,
            participant_b_id=b_id,
            participant_a_type='human',
            participant_b_type='human',
            status='active',
        )
        session.add(conv)
        await session.flush()
        conv_id = str(conv.id)

        msg = HasnMessages(
            conversation_id=conv_id,
            from_id=a_id,
            from_type=1,
            to_id=b_id,
            to_type=1,
            content_type=1,
            content={'text': '断连前发出的第一条'},
            msg_type='message',
            status=1,
            local_id=local_id,
        )
        session.add(msg)
        await session.flush()
        msg_id = msg.id
        await session.commit()
        return conv_id, msg_id


async def _count_messages_with_local_id(sessionmaker, local_id: str) -> int:
    async with sessionmaker() as session:
        return (
            await session.execute(
                sa.text('SELECT count(*) FROM public.hasn_messages WHERE local_id = :lid'),
                {'lid': local_id},
            )
        ).scalar()


async def _cleanup_pair(sessionmaker, a_id: str, b_id: str) -> None:
    async with sessionmaker() as session:
        ids = (
            (
                await session.execute(
                    sa.text(
                        'SELECT id FROM public.hasn_conversations '
                        'WHERE participant_a_id IN (:a, :b) OR participant_b_id IN (:a, :b)'
                    ),
                    {'a': a_id, 'b': b_id},
                )
            )
            .scalars()
            .all()
        )
        if ids:
            await session.execute(
                sa.text('DELETE FROM public.hasn_messages WHERE conversation_id = ANY(:ids)'),
                {'ids': list(ids)},
            )
            await session.execute(
                sa.text('DELETE FROM public.hasn_conversations WHERE id = ANY(:ids)'),
                {'ids': list(ids)},
            )
        await session.commit()


async def test_route_message_dedups_on_local_id(sessionmaker_pg) -> None:
    """重发同一 local_id：回原 msg_id+conversation_id、deduped=True、不二次落库。"""
    a_id, b_id = _fresh_pair()
    local_id = f'lid-{uuid.uuid4().hex}'
    try:
        conv_id, msg_id = await _seed_conversation_with_message(sessionmaker_pg, a_id, b_id, local_id)

        # daemon 重发：相同 local_id 再次进 route_message
        async with sessionmaker_pg() as session:
            result = await route_message(
                db=session,
                from_id=a_id,
                to_target=b_id,
                content={'text': '断连前发出的第一条'},
                content_type=1,
                local_id=local_id,
            )
            await session.commit()

        assert result.get('error') is False, f'去重命中应是成功返回，实际 {result}'
        assert result.get('deduped') is True, f'应标记 deduped=True，实际 {result}'
        assert result['msg_id'] == msg_id, '应回既有 msg_id，不新建'
        assert str(result['conversation_id']) == str(conv_id), '应回既有 conversation_id'
        assert result['status'] == 'sent'
        assert result['local_id'] == local_id

        count = await _count_messages_with_local_id(sessionmaker_pg, local_id)
        assert count == 1, f'重发不得二次落库，该 local_id 应仍只 1 行，实际 {count}'
    finally:
        await _cleanup_pair(sessionmaker_pg, a_id, b_id)


async def test_route_message_dedup_is_repeatable(sessionmaker_pg) -> None:
    """多次重发（≥2 次）都稳定回同一行、始终不落新行（出站队列可能重发多轮）。"""
    a_id, b_id = _fresh_pair()
    local_id = f'lid-{uuid.uuid4().hex}'
    try:
        _conv_id, msg_id = await _seed_conversation_with_message(sessionmaker_pg, a_id, b_id, local_id)

        for _ in range(3):
            async with sessionmaker_pg() as session:
                result = await route_message(
                    db=session,
                    from_id=a_id,
                    to_target=b_id,
                    content={'text': '断连前发出的第一条'},
                    content_type=1,
                    local_id=local_id,
                )
                await session.commit()
            assert result.get('deduped') is True
            assert result['msg_id'] == msg_id

        count = await _count_messages_with_local_id(sessionmaker_pg, local_id)
        assert count == 1, f'多轮重发后仍只 1 行，实际 {count}'
    finally:
        await _cleanup_pair(sessionmaker_pg, a_id, b_id)

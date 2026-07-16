"""R1-02 验收：PythonLocalImGateway 包装行为与现网 route_message 逐字节一致（真实 PG，零 mock）。

R1-05 切片①把 `hasn.message.send` 的 direct 路径切到 ImGateway port。port 第一版内部仍复用
现网 `route_message` / `get_or_create_conversation`，故落库/幂等/受众扇出必须与切换前**完全一致**。
本组用真 PG（DATABASE_PORT=15432）驱动 port 本体（不经工具、不打 stub），钉两件事：

1. **ensure 幂等**：同一对参与者反复 ensure → 命中同一 direct 会话（包装 get_or_create 的 advisory lock）；
2. **send 幂等去重逐字一致**：对已落库的 idempotency_key（= WS local_id）重发 → port 回
   `ACCEPTED + deduped=True + 原 message_id/conversation_id`，且**不二次落库**——与现网 route_message
   step 0 去重语义完全对齐（先于目标解析/关系/权限，见 test_message_send_idempotent 的现网基线）。

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
from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.app.hasn_im.ports.dto import (
    ActorKind,
    DeliveryState,
    EnsureDirectConversationCommand,
    SendMessageCommand,
    ServicePrincipal,
)
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
        pytest.skip(f'PostgreSQL 不可达，跳过 ImGateway 契约测试：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _fresh_pair() -> tuple[str, str]:
    return f'h_gw{uuid.uuid4().hex[:18]}', f'h_gw{uuid.uuid4().hex[:18]}'


def _principal(sender: str) -> ServicePrincipal:
    return ServicePrincipal(canonical_sender=sender, actor_kind=ActorKind.HUMAN)


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
                {'ids': ids},
            )
            await session.execute(
                sa.text('DELETE FROM public.hasn_conversations WHERE id = ANY(:ids)'), {'ids': ids}
            )
            await session.commit()


async def test_ensure_direct_conversation_is_idempotent(sessionmaker_pg) -> None:
    """同一对参与者反复 ensure → 命中同一 direct 会话（包装 get_or_create 幂等）。"""
    a_id, b_id = _fresh_pair()
    gw = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        ref1 = await gw.ensure_direct_conversation(
            EnsureDirectConversationCommand(peer_hasn_id=b_id), _principal(a_id)
        )
        ref2 = await gw.ensure_direct_conversation(
            EnsureDirectConversationCommand(peer_hasn_id=b_id), _principal(a_id)
        )
        assert ref1.conversation_id == ref2.conversation_id, 'ensure 必须幂等命中同一会话'
        assert ref1.conversation_type == 'direct'
    finally:
        await _cleanup_pair(sessionmaker_pg, a_id, b_id)


async def test_send_dedups_by_idempotency_key_byte_identical(sessionmaker_pg) -> None:
    """对已落库 idempotency_key 重发 → ACCEPTED+deduped，回原 msg_id/conv_id，不二次落库。"""
    a_id, b_id = _fresh_pair()
    local_id = f'lid_{uuid.uuid4().hex}'
    conv_id, seeded_msg_id = await _seed_conversation_with_message(sessionmaker_pg, a_id, b_id, local_id)
    gw = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        result = await gw.send_message(
            SendMessageCommand(
                conversation_id=conv_id,
                content={'text': '重连补投的同一条'},
                idempotency_key=local_id,
            ),
            _principal(a_id),
        )
        # route_message step 0 去重命中 → 逐字与现网一致：ACCEPTED + deduped + 原 id。
        assert result.delivery_state == DeliveryState.ACCEPTED
        assert result.deduped is True
        assert result.message_id == seeded_msg_id
        assert result.conversation_id == conv_id
        # 不二次落库：同 local_id 仍只有一行。
        assert await _count_messages_with_local_id(sessionmaker_pg, local_id) == 1
    finally:
        await _cleanup_pair(sessionmaker_pg, a_id, b_id)

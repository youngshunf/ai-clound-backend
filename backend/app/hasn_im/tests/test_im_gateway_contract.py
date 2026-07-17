"""ImGateway contract suite 首版（R1-02·真实 PG·零 mock）。

验证 `PythonLocalImGateway`（包装现网 route_message/get_or_create_conversation）满足
`ImGateway` port 契约、且**行为与现网一致**：

1. ensure_direct_conversation 幂等——同一对参与者反复 ensure 收敛到同一 conversation_id；
2. send_message conversation_id-first——缺会话抛 ImConversationNotFound（不静默）；
3. 幂等 dedup 映射——同 idempotency_key 命中既有 local_id → delivery_state=ACCEPTED +
   deduped=True + 不二次落库（现网 route step 0 语义经 port 忠实透出）；
4. 返回体形态契约——任何 direct 发送要么返回良构 SendMessageResult（三态之一 + 会话 id
   一致），要么抛协议级 ImSendRejected（硬拒非投递态），绝不返回畸形结构。

R1-05 各切片把真实调用方切到本 port 后，这套 suite 作为「包装版=现网」的回归锚点，并在
R5-01 固化为 Rust adapter 差分对跑的长期资产。

需要本地 PG（export DATABASE_PORT=15432）；不可达则跳过。每用例用 uuid 派生全新参与者、
末尾清理自身行，不污染库。
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
from backend.app.hasn_im.application.errors import ImConversationNotFound, ImSendRejected
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
        pytest.skip(f'PostgreSQL 不可达，跳过 ImGateway 契约套件：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _fresh_pair() -> tuple[str, str]:
    """全新一对人类参与者（h_ 前缀，避免撞既有会话/关系）。"""
    return f'h_ct{uuid.uuid4().hex[:18]}', f'h_ct{uuid.uuid4().hex[:18]}'


def _principal(sender: str) -> ServicePrincipal:
    return ServicePrincipal(canonical_sender=sender, actor_kind=ActorKind.HUMAN, origin_node_id='cloud')


async def _cleanup(sessionmaker, *ids: str) -> None:
    async with sessionmaker() as session:
        conv_ids = (
            (
                await session.execute(
                    sa.text(
                        'SELECT id FROM public.hasn_conversations '
                        'WHERE participant_a_id = ANY(:ids) OR participant_b_id = ANY(:ids)'
                    ),
                    {'ids': list(ids)},
                )
            )
            .scalars()
            .all()
        )
        if conv_ids:
            await session.execute(
                sa.text('DELETE FROM public.hasn_messages WHERE conversation_id = ANY(:cids)'),
                {'cids': [str(c) for c in conv_ids]},
            )
            await session.execute(
                sa.text('DELETE FROM public.hasn_conversations WHERE id = ANY(:cids)'),
                {'cids': [str(c) for c in conv_ids]},
            )
        await session.commit()


async def test_ensure_direct_conversation_is_idempotent(sessionmaker_pg):
    a, b = _fresh_pair()
    gw = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        ref1 = await gw.ensure_direct_conversation(
            EnsureDirectConversationCommand(peer_hasn_id=b), _principal(a)
        )
        ref2 = await gw.ensure_direct_conversation(
            EnsureDirectConversationCommand(peer_hasn_id=b), _principal(a)
        )
        # 反向发起（b→a）也应收敛到同一会话（参与者对排序无关）
        ref3 = await gw.ensure_direct_conversation(
            EnsureDirectConversationCommand(peer_hasn_id=a), _principal(b)
        )
        assert ref1.conversation_id == ref2.conversation_id == ref3.conversation_id
        assert ref1.conversation_type == 'direct'
    finally:
        await _cleanup(sessionmaker_pg, a, b)


async def test_send_to_missing_conversation_raises(sessionmaker_pg):
    a, b = _fresh_pair()
    gw = PythonLocalImGateway(session_factory=sessionmaker_pg)
    missing = str(uuid.uuid4())
    with pytest.raises(ImConversationNotFound):
        await gw.send_message(
            SendMessageCommand(conversation_id=missing, content={'text': '你好'}),
            _principal(a),
        )


async def test_send_dedup_maps_to_accepted_no_double_persist(sessionmaker_pg):
    """同 idempotency_key 命中既有 local_id → ACCEPTED + deduped，且不二次落库。"""
    a, b = _fresh_pair()
    local_id = f'ct-dedup-{uuid.uuid4().hex[:12]}'
    gw = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        # 直接播一条 direct 会话 + 一条带 local_id 的已落库消息（模拟断连前发出）
        async with sessionmaker_pg() as session:
            lo, hi = sorted([a, b])
            conv = HasnConversations(
                type='direct',
                relation_type='social',
                participant_a_id=lo,
                participant_b_id=hi,
                participant_a_type='human',
                participant_b_type='human',
                status='active',
            )
            session.add(conv)
            await session.flush()
            conv_id = str(conv.id)
            seeded = HasnMessages(
                conversation_id=conv_id,
                from_id=a,
                from_type=1,
                to_id=b,
                to_type=1,
                content_type=1,
                content={'text': '断连前发出'},
                msg_type='message',
                status=1,
                local_id=local_id,
            )
            session.add(seeded)
            await session.flush()
            seeded_id = seeded.id
            await session.commit()

        result = await gw.send_message(
            SendMessageCommand(
                conversation_id=conv_id,
                content={'text': '重连补发'},
                idempotency_key=local_id,
            ),
            _principal(a),
        )

        assert result.delivery_state == DeliveryState.ACCEPTED
        assert result.deduped is True
        assert result.message_id == seeded_id
        assert result.conversation_id == conv_id

        # 未二次落库：该 local_id 仍只有一行
        async with sessionmaker_pg() as session:
            count = (
                await session.execute(
                    sa.text('SELECT count(*) FROM public.hasn_messages WHERE local_id = :lid'),
                    {'lid': local_id},
                )
            ).scalar()
        assert count == 1
    finally:
        await _cleanup(sessionmaker_pg, a, b)


async def test_send_returns_wellformed_result_or_rejects(sessionmaker_pg):
    """新鲜 direct 发送：良构 SendMessageResult（三态之一 + 会话一致）或干净 ImSendRejected。"""
    a, b = _fresh_pair()
    gw = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        ref = await gw.ensure_direct_conversation(
            EnsureDirectConversationCommand(peer_hasn_id=b), _principal(a)
        )
        try:
            result = await gw.send_message(
                SendMessageCommand(
                    conversation_id=ref.conversation_id,
                    content={'text': '契约形态校验'},
                    idempotency_key=f'ct-fresh-{uuid.uuid4().hex[:12]}',
                ),
                _principal(a),
            )
        except ImSendRejected as rejected:
            # 协议级硬拒也是良构契约（非投递态，携带 code+message）
            assert rejected.code
            assert rejected.message
            return
        assert isinstance(result.delivery_state, DeliveryState)
        assert result.conversation_id == ref.conversation_id
        if result.delivery_state == DeliveryState.ACCEPTED:
            assert result.message_id is not None
    finally:
        await _cleanup(sessionmaker_pg, a, b)

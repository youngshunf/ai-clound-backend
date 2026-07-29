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

import asyncio
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import (
    HasnConversationMemberships,
    HasnConversations,
    HasnMessages,
    HasnUnreadProjection,
)
from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_im.application.errors import (
    ImConversationNotFound,
    ImSenderNotParticipant,
    ImSendRejected,
)
from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.app.hasn_im.ports.dto import (
    ActorKind,
    DeliveryState,
    EnsureDirectConversationCommand,
    ListConversationsQuery,
    ListMessagesQuery,
    ReadCursorCommand,
    RecallMessageCommand,
    SendMessageCommand,
    ServicePrincipal,
    UpdateGroupMembersCommand,
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


async def _seed_humans(
    sessionmaker,
    *hasn_ids: str,
    status: str = 'active',
) -> None:
    """建立真实人类身份，供发送前置的身份存活校验使用。"""
    async with sessionmaker() as session:
        for hasn_id in hasn_ids:
            marker = uuid.uuid4().hex
            session.add(
                HasnHumans(
                    hasn_id=hasn_id,
                    star_id=f'h{marker[:24]}',
                    user_id=int(marker[:15], 16),
                    nickname=f'契约测试主人{marker[:10]}',
                    status=status,
                )
            )
        await session.commit()


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
                sa.text(
                    'DELETE FROM public.hasn_im_integration_events '
                    'WHERE aggregate_id = ANY(:cids)'
                ),
                {'cids': [str(c) for c in conv_ids]},
            )
            await session.execute(
                sa.text('DELETE FROM public.hasn_messages WHERE conversation_id = ANY(:cids)'),
                {'cids': [str(c) for c in conv_ids]},
            )
            await session.execute(
                sa.text(
                    'DELETE FROM public.hasn_unread_projection '
                    'WHERE conversation_id = ANY(:cids)'
                ),
                {'cids': [str(c) for c in conv_ids]},
            )
            await session.execute(
                sa.text(
                    'DELETE FROM public.hasn_conversation_memberships '
                    'WHERE conversation_id = ANY(:cids)'
                ),
                {'cids': [str(c) for c in conv_ids]},
            )
            await session.execute(
                sa.text('DELETE FROM public.hasn_conversations WHERE id = ANY(:cids)'),
                {'cids': [str(c) for c in conv_ids]},
            )
        await session.execute(
            sa.delete(HasnAgents).where(HasnAgents.hasn_id.in_(list(ids)))
        )
        await session.execute(
            sa.delete(HasnHumans).where(HasnHumans.hasn_id.in_(list(ids)))
        )
        await session.commit()


async def test_ensure_direct_conversation_is_idempotent(sessionmaker_pg):
    a, b = _fresh_pair()
    gw = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_humans(sessionmaker_pg, a, b)
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
    a = f'h_ct{uuid.uuid4().hex[:18]}'
    b = f'a_ct{uuid.uuid4().hex[:18]}'
    local_id = f'ct-dedup-{uuid.uuid4().hex[:12]}'
    gw = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_humans(sessionmaker_pg, a)
        # 直接播一条 direct 会话 + 一条带 local_id 的已落库消息（模拟断连前发出）
        async with sessionmaker_pg() as session:
            marker = uuid.uuid4().hex
            session.add(
                HasnAgents(
                    hasn_id=b,
                    star_id=f'a{marker[:24]}',
                    owner_id=a,
                    display_name='幂等测试分身',
                    agent_name=f'idem{marker[:12]}',
                    api_key_hash=marker,
                    status='active',
                    created_via='client',
                )
            )
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
                to_type=2,
                content_type=1,
                content={'text': '重连补发'},
                msg_type='message',
                status=1,
                priority='normal',
                local_id=local_id,
                origin_node_id='cloud',
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


async def test_send_same_idempotency_key_with_different_payload_conflicts(
    sessionmaker_pg,
):
    """同一 local_id 不能静默吞掉不同 payload。"""
    a = f'h_ct{uuid.uuid4().hex[:18]}'
    b = f'a_ct{uuid.uuid4().hex[:18]}'
    local_id = f'ct-conflict-{uuid.uuid4().hex[:12]}'
    gw = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_humans(sessionmaker_pg, a)
        marker = uuid.uuid4().hex
        async with sessionmaker_pg() as session:
            session.add(
                HasnAgents(
                    hasn_id=b,
                    star_id=f'a{marker[:24]}',
                    owner_id=a,
                    display_name='冲突测试分身',
                    agent_name=f'conflict{marker[:10]}',
                    api_key_hash=marker,
                    status='active',
                    created_via='client',
                )
            )
            await session.commit()
        reference = await gw.ensure_direct_conversation(
            EnsureDirectConversationCommand(peer_hasn_id=b),
            _principal(a),
        )
        first = await gw.send_message(
            SendMessageCommand(
                conversation_id=reference.conversation_id,
                content={'text': '第一份载荷'},
                idempotency_key=local_id,
            ),
            _principal(a),
        )
        assert first.delivery_state is DeliveryState.ACCEPTED

        with pytest.raises(ImSendRejected) as caught:
            await gw.send_message(
                SendMessageCommand(
                    conversation_id=reference.conversation_id,
                    content={'text': '第二份不同载荷'},
                    idempotency_key=local_id,
                ),
                _principal(a),
            )
        assert caught.value.code == 3015
        assert 'local_id' in caught.value.message
    finally:
        await _cleanup(sessionmaker_pg, a, b)


async def test_concurrent_same_idempotency_key_commits_once_without_sequence_gap(
    sessionmaker_pg,
):
    """两个独立事务并发发送同一命令时，只提交一次且不消耗额外会话序号。"""
    a = f'h_ct{uuid.uuid4().hex[:18]}'
    b = f'a_ct{uuid.uuid4().hex[:18]}'
    local_id = f'ct-race-{uuid.uuid4().hex[:12]}'
    gw = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_humans(sessionmaker_pg, a)
        marker = uuid.uuid4().hex
        async with sessionmaker_pg() as session:
            session.add(
                HasnAgents(
                    hasn_id=b,
                    star_id=f'a{marker[:24]}',
                    owner_id=a,
                    display_name='并发幂等测试分身',
                    agent_name=f'race{marker[:12]}',
                    api_key_hash=marker,
                    status='active',
                    created_via='client',
                )
            )
            await session.commit()
        reference = await gw.ensure_direct_conversation(
            EnsureDirectConversationCommand(peer_hasn_id=b),
            _principal(a),
        )
        command = SendMessageCommand(
            conversation_id=reference.conversation_id,
            content={'text': '同一命令并发重放'},
            idempotency_key=local_id,
        )

        first, second = await asyncio.gather(
            gw.send_message(command, _principal(a)),
            gw.send_message(command, _principal(a)),
        )

        assert first.message_id == second.message_id
        assert sorted([first.deduped, second.deduped]) == [False, True]
        async with sessionmaker_pg() as session:
            message_count = (
                await session.execute(
                    sa.text(
                        'SELECT count(*) FROM public.hasn_messages '
                        'WHERE local_id = :local_id'
                    ),
                    {'local_id': local_id},
                )
            ).scalar_one()
            event_count = (
                await session.execute(
                    sa.text(
                        'SELECT count(*) FROM public.hasn_im_integration_events '
                        "WHERE aggregate_id = :conversation_id "
                        "AND event_type = 'im.message.committed.v1'"
                    ),
                    {'conversation_id': reference.conversation_id},
                )
            ).scalar_one()
            current_seq = (
                await session.execute(
                    sa.text(
                        'SELECT current_seq FROM public.hasn_conversations '
                        'WHERE id = CAST(:conversation_id AS uuid)'
                    ),
                    {'conversation_id': reference.conversation_id},
                )
            ).scalar_one()
        assert int(message_count) == 1
        assert int(event_count) == 1
        assert int(current_seq) == 1
    finally:
        await _cleanup(sessionmaker_pg, a, b)


async def test_send_by_non_participant_rejected(sessionmaker_pg):
    """权限负测（R2 authz 面）：非会话参与者向该 direct 会话发送必被拒。

    发送方既非 participant_a 也非 participant_b → `_counterpart` 反解时抛
    `ImSenderNotParticipant`（在 route_message 之前拦截），绝不静默落库到别人的会话。"""
    a, b = _fresh_pair()
    intruder = f'h_ct{uuid.uuid4().hex[:18]}'  # 第三方——非本会话参与者
    gw = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_humans(sessionmaker_pg, a, b, intruder)
        ref = await gw.ensure_direct_conversation(
            EnsureDirectConversationCommand(peer_hasn_id=b), _principal(a)
        )
        with pytest.raises(ImSenderNotParticipant):
            await gw.send_message(
                SendMessageCommand(
                    conversation_id=ref.conversation_id,
                    content={'text': '越权插话'},
                    idempotency_key=f'ct-intruder-{uuid.uuid4().hex[:12]}',
                ),
                _principal(intruder),
            )
        # 副作用断言：该会话不因越权发送而多出任何消息行
        async with sessionmaker_pg() as session:
            count = (
                await session.execute(
                    sa.text(
                        'SELECT count(*) FROM public.hasn_messages WHERE conversation_id = :cid'
                    ),
                    {'cid': str(ref.conversation_id)},
                )
            ).scalar()
        assert count == 0
    finally:
        await _cleanup(sessionmaker_pg, a, b, intruder)


async def test_send_returns_wellformed_result_or_rejects(sessionmaker_pg):
    """新鲜 direct 发送：良构 SendMessageResult（三态之一 + 会话一致）或干净 ImSendRejected。"""
    a, b = _fresh_pair()
    gw = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_humans(sessionmaker_pg, a, b)
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


async def test_missing_or_inactive_sender_is_rejected(sessionmaker_pg):
    """已存在会话也不能让缺失或停用身份继续发送。"""
    active, peer = _fresh_pair()
    inactive = f'h_ct{uuid.uuid4().hex[:18]}'
    missing = f'h_ct{uuid.uuid4().hex[:18]}'
    gw = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_humans(sessionmaker_pg, active, peer)
        await _seed_humans(sessionmaker_pg, inactive, status='suspended')
        reference = await gw.ensure_direct_conversation(
            EnsureDirectConversationCommand(peer_hasn_id=peer),
            _principal(active),
        )
        for invalid_sender in (inactive, missing):
            with pytest.raises(ImSendRejected, match='身份'):
                await gw.send_message(
                    SendMessageCommand(
                        conversation_id=reference.conversation_id,
                        content={'text': '不应落库'},
                        idempotency_key=f'ct-invalid-{uuid.uuid4().hex[:10]}',
                    ),
                    _principal(invalid_sender),
                )
    finally:
        await _cleanup(sessionmaker_pg, active, peer, inactive, missing)


async def test_send_as_other_owners_agent_is_rejected(sessionmaker_pg):
    """主人只能代发自己名下的活动分身，不能伪造其他主人的 Agent。"""
    caller, actual_owner = _fresh_pair()
    peer = f'h_ct{uuid.uuid4().hex[:18]}'
    agent = f'a_ct{uuid.uuid4().hex[:18]}'
    marker = uuid.uuid4().hex
    gw = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_humans(sessionmaker_pg, caller, actual_owner, peer)
        async with sessionmaker_pg() as session:
            session.add(
                HasnAgents(
                    hasn_id=agent,
                    star_id=f'a{marker[:24]}',
                    owner_id=actual_owner,
                    display_name='他人分身',
                    agent_name=f'foreign{marker[:10]}',
                    api_key_hash=marker,
                    status='active',
                    created_via='client',
                )
            )
            await session.commit()
        with pytest.raises(ImSendRejected, match='不是认证主人名下'):
            await gw.ensure_direct_conversation(
                EnsureDirectConversationCommand(peer_hasn_id=peer),
                ServicePrincipal(
                    canonical_sender=caller,
                    actor_kind=ActorKind.HUMAN,
                    send_as=agent,
                ),
            )
    finally:
        await _cleanup(sessionmaker_pg, caller, actual_owner, peer, agent)


async def _seed_epoch_visibility_fixture(
    sessionmaker,
    viewer: str,
    peer: str,
) -> tuple[str, list[int]]:
    """建立两段成员周期与六条真实消息，返回会话和消息 ID。"""
    async with sessionmaker() as session:
        lo, hi = sorted([viewer, peer])
        conversation = HasnConversations(
            type='direct',
            relation_type='social',
            participant_a_id=lo,
            participant_b_id=hi,
            participant_a_type='human',
            participant_b_type='human',
            status='active',
            current_seq=6,
            message_count=6,
        )
        session.add(conversation)
        await session.flush()
        conversation_id = str(conversation.id)
        session.add_all(
            [
                HasnConversationMemberships(
                    conversation_id=conversation_id,
                    member_hasn_id=viewer,
                    member_type='human',
                    joined_seq=1,
                    left_seq=2,
                    read_seq=2,
                    state='left',
                ),
                HasnConversationMemberships(
                    conversation_id=conversation_id,
                    member_hasn_id=viewer,
                    member_type='human',
                    joined_seq=5,
                    read_seq=4,
                    state='active',
                ),
                HasnConversationMemberships(
                    conversation_id=conversation_id,
                    member_hasn_id=peer,
                    member_type='human',
                    joined_seq=1,
                    read_seq=0,
                    state='active',
                ),
            ]
        )
        messages = []
        for sequence in range(1, 7):
            message = HasnMessages(
                conversation_id=conversation_id,
                conversation_seq=sequence,
                from_id=peer,
                from_type=1,
                to_id=viewer,
                to_type=1,
                content_type=1,
                content={'text': f'消息{sequence}'},
                msg_type='message',
                status=1,
                priority='normal',
                local_id=f'epoch-{uuid.uuid4().hex}',
            )
            session.add(message)
            messages.append(message)
        await session.flush()
        conversation.last_message_id = messages[-1].id
        conversation.last_message_preview = '消息6'
        conversation.last_message_from = peer
        session.add(
            HasnUnreadProjection(
                conversation_id=conversation_id,
                member_hasn_id=viewer,
                unread_count=2,
                computed_at_seq=6,
            )
        )
        await session.commit()
        return conversation_id, [message.id for message in messages]


async def test_list_messages_honors_all_membership_epochs_and_cursor(
    sessionmaker_pg,
):
    """列表只返回两个可见周期，离开期间消息不可见且游标稳定。"""
    viewer, peer = _fresh_pair()
    gateway = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_humans(sessionmaker_pg, viewer, peer)
        conversation_id, _ = await _seed_epoch_visibility_fixture(
            sessionmaker_pg,
            viewer,
            peer,
        )
        first = await gateway.list_messages(
            ListMessagesQuery(conversation_id=conversation_id, limit=2),
            _principal(viewer),
        )
        assert [item['context']['conversation_seq'] for item in first.items] == [5, 6]
        assert first.has_more is True
        assert first.next_cursor is not None

        second = await gateway.list_messages(
            ListMessagesQuery(
                conversation_id=conversation_id,
                limit=2,
                before_cursor=first.next_cursor,
            ),
            _principal(viewer),
        )
        assert [item['context']['conversation_seq'] for item in second.items] == [1, 2]
        assert second.has_more is False
        assert second.next_cursor is None
    finally:
        await _cleanup(sessionmaker_pg, viewer, peer)


async def test_list_conversations_uses_active_membership_and_projection(
    sessionmaker_pg,
):
    """会话列表以活动 membership 判权并批量投影对端资料和未读数。"""
    viewer, peer = _fresh_pair()
    outsider = f'h_ct{uuid.uuid4().hex[:18]}'
    gateway = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_humans(sessionmaker_pg, viewer, peer, outsider)
        conversation_id, _ = await _seed_epoch_visibility_fixture(
            sessionmaker_pg,
            viewer,
            peer,
        )
        page = await gateway.list_conversations(
            ListConversationsQuery(limit=20),
            _principal(viewer),
        )
        item = next(value for value in page.items if value['id'] == conversation_id)
        assert item['peer_id'] == peer
        assert item['peer_name'].startswith('契约测试主人')
        assert item['unread_count'] == 2

        outsider_page = await gateway.list_conversations(
            ListConversationsQuery(limit=20),
            _principal(outsider),
        )
        assert all(value['id'] != conversation_id for value in outsider_page.items)
    finally:
        await _cleanup(sessionmaker_pg, viewer, peer, outsider)


async def test_advance_read_cursor_is_monotonic_and_rebuilds_projection(
    sessionmaker_pg,
):
    """read_seq 只进不退，推进后未读投影由权威消息重新计算。"""
    viewer, peer = _fresh_pair()
    gateway = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_humans(sessionmaker_pg, viewer, peer)
        conversation_id, message_ids = await _seed_epoch_visibility_fixture(
            sessionmaker_pg,
            viewer,
            peer,
        )
        advanced = await gateway.advance_read_cursor(
            ReadCursorCommand(
                conversation_id=conversation_id,
                up_to_message_id=message_ids[-1],
            ),
            _principal(viewer),
        )
        assert advanced == 6
        stale = await gateway.advance_read_cursor(
            ReadCursorCommand(
                conversation_id=conversation_id,
                up_to_seq=5,
            ),
            _principal(viewer),
        )
        assert stale == 6

        async with sessionmaker_pg() as session:
            membership = (
                await session.execute(
                    sa.select(HasnConversationMemberships).where(
                        HasnConversationMemberships.conversation_id
                        == conversation_id,
                        HasnConversationMemberships.member_hasn_id == viewer,
                        HasnConversationMemberships.left_seq.is_(None),
                    )
                )
            ).scalar_one()
            projection = (
                await session.execute(
                    sa.select(HasnUnreadProjection).where(
                        HasnUnreadProjection.conversation_id == conversation_id,
                        HasnUnreadProjection.member_hasn_id == viewer,
                    )
                )
            ).scalar_one()
            old_count = (
                await session.execute(
                    sa.text(
                        'SELECT count(*) FROM public.hasn_unread_counts '
                        'WHERE hasn_id = :viewer AND conversation_id = :conversation_id'
                    ),
                    {
                        'viewer': viewer,
                        'conversation_id': conversation_id,
                    },
                )
            ).scalar_one()
        assert membership.read_seq == 6
        assert projection.unread_count == 0
        assert projection.computed_at_seq == 6
        assert old_count == 0
    finally:
        await _cleanup(sessionmaker_pg, viewer, peer)


async def test_recall_message_is_transactional_idempotent_and_rebuilds_unread(
    sessionmaker_pg,
) -> None:
    """撤回事实、未读投影与集成事件必须原子提交，重复撤回幂等。"""
    viewer, sender = _fresh_pair()
    gateway = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_humans(sessionmaker_pg, viewer, sender)
        conversation_id, message_ids = await _seed_epoch_visibility_fixture(
            sessionmaker_pg,
            viewer,
            sender,
        )
        first = await gateway.recall_message(
            RecallMessageCommand(
                conversation_id=conversation_id,
                message_id=message_ids[-1],
            ),
            _principal(sender),
        )
        second = await gateway.recall_message(
            RecallMessageCommand(
                conversation_id=conversation_id,
                message_id=message_ids[-1],
            ),
            _principal(sender),
        )

        assert first.delivery_state is DeliveryState.ACCEPTED
        assert first.message_id == message_ids[-1]
        assert first.conversation_seq == 6
        assert first.deduped is False
        assert second.message_id == first.message_id
        assert second.deduped is True

        async with sessionmaker_pg() as session:
            message = await session.get(HasnMessages, message_ids[-1])
            unread = await session.scalar(
                sa.select(HasnUnreadProjection.unread_count).where(
                    HasnUnreadProjection.conversation_id == conversation_id,
                    HasnUnreadProjection.member_hasn_id == viewer,
                )
            )
            event_count = await session.scalar(
                sa.text(
                    'SELECT count(*) '
                    'FROM public.hasn_im_integration_events '
                    "WHERE aggregate_id = :conversation_id "
                    "AND event_type = 'im.message.recalled.v1' "
                    "AND payload->>'message_id' = :message_id"
                ),
                {
                    'conversation_id': conversation_id,
                    'message_id': str(message_ids[-1]),
                },
            )
        assert message is not None
        assert message.status == 4
        assert message.recalled_by == sender
        assert message.recalled_at is not None
        assert unread == 1
        assert event_count == 1
    finally:
        await _cleanup(sessionmaker_pg, viewer, sender)


async def test_update_group_members_closes_and_reopens_membership_epochs(
    sessionmaker_pg,
) -> None:
    """群成员退出闭合旧周期，重入创建新周期，序号游标不回退。"""
    owner, member = _fresh_pair()
    newcomer = f'h_ct{uuid.uuid4().hex[:18]}'
    outsider = f'h_ct{uuid.uuid4().hex[:18]}'
    gateway = PythonLocalImGateway(session_factory=sessionmaker_pg)
    try:
        await _seed_humans(
            sessionmaker_pg,
            owner,
            member,
            newcomer,
            outsider,
        )
        async with sessionmaker_pg() as session:
            conversation = HasnConversations(
                type='group',
                relation_type='social',
                participant_a_id=owner,
                participant_a_type='human',
                group_id=f'g:{int(uuid.uuid4().hex[:7], 16) % 1_000_000:06d}',
                group_name='成员周期契约群',
                group_owner_id=owner,
                status='active',
                current_seq=5,
                revision=1,
                member_count=2,
            )
            session.add(conversation)
            await session.flush()
            conversation_id = str(conversation.id)
            session.add_all(
                [
                    HasnConversationMemberships(
                        conversation_id=conversation_id,
                        member_hasn_id=owner,
                        member_type='human',
                        role='owner',
                        joined_seq=1,
                        read_seq=5,
                        state='active',
                    ),
                    HasnConversationMemberships(
                        conversation_id=conversation_id,
                        member_hasn_id=member,
                        member_type='human',
                        role='member',
                        joined_seq=1,
                        read_seq=3,
                        state='active',
                    ),
                ]
            )
            await session.commit()

        await gateway.update_group_members(
            UpdateGroupMembersCommand(
                conversation_id=conversation_id,
                add=[newcomer],
                remove=[member],
            ),
            _principal(owner),
        )
        await gateway.update_group_members(
            UpdateGroupMembersCommand(
                conversation_id=conversation_id,
                add=[member],
            ),
            _principal(owner),
        )

        with pytest.raises(ImSendRejected, match='群成员'):
            await gateway.update_group_members(
                UpdateGroupMembersCommand(
                    conversation_id=conversation_id,
                    add=[outsider],
                ),
                _principal(outsider),
            )

        async with sessionmaker_pg() as session:
            conversation = await session.get(
                HasnConversations,
                uuid.UUID(conversation_id),
            )
            epochs = list(
                (
                    await session.execute(
                        sa.select(HasnConversationMemberships)
                        .where(
                            HasnConversationMemberships.conversation_id
                            == conversation_id,
                            HasnConversationMemberships.member_hasn_id.in_(
                                [member, newcomer]
                            ),
                        )
                        .order_by(HasnConversationMemberships.id)
                    )
                )
                .scalars()
                .all()
            )
        member_epochs = [
            epoch for epoch in epochs if epoch.member_hasn_id == member
        ]
        newcomer_epoch = next(
            epoch for epoch in epochs if epoch.member_hasn_id == newcomer
        )
        assert conversation is not None
        assert conversation.current_seq == 5
        assert conversation.member_count == 3
        assert conversation.revision == 3
        assert len(member_epochs) == 2
        assert member_epochs[0].left_seq == 5
        assert member_epochs[0].state == 'removed'
        assert member_epochs[1].joined_seq == 6
        assert member_epochs[1].read_seq == 5
        assert member_epochs[1].left_seq is None
        assert newcomer_epoch.joined_seq == 6
        assert newcomer_epoch.left_seq is None
    finally:
        await _cleanup(
            sessionmaker_pg,
            owner,
            member,
            newcomer,
            outsider,
        )

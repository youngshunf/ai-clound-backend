"""消息历史快照恢复契约（真实 PostgreSQL，零 mock）。"""

from __future__ import annotations

import uuid

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import (
    HasnConversationMemberships,
    HasnConversations,
    HasnImHistorySnapshots,
    HasnMessages,
)
from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_im.application.history_snapshot import (
    HistorySnapshotTokenError,
    list_history_snapshot_conversations,
    list_history_snapshot_messages,
    start_history_snapshot,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def sessionmaker_pg() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        SQLALCHEMY_DATABASE_URL,
        poolclass=NullPool,
        future=True,
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text('SELECT 1'))
    except OperationalError as exc:
        await engine.dispose()
        pytest.skip(f'PostgreSQL 不可达，跳过消息历史快照契约：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _human(prefix: str) -> str:
    return f'h_{prefix}{uuid.uuid4().hex[:16]}'


def _agent(prefix: str) -> str:
    return f'a_{prefix}{uuid.uuid4().hex[:16]}'


def _seed_humans(session: AsyncSession, *hasn_ids: str) -> None:
    for hasn_id in hasn_ids:
        marker = uuid.uuid4().hex
        session.add(
            HasnHumans(
                hasn_id=hasn_id,
                star_id=f'h{marker[:24]}',
                user_id=int(marker[:15], 16),
                nickname=f'快照测试用户{marker[:8]}',
                status='active',
            )
        )


async def _seed_direct_conversation(
    session: AsyncSession,
    *,
    left: str,
    right: str,
    messages: list[tuple[str, str, str]],
) -> str:
    participant_a, participant_b = sorted((left, right))
    conversation = HasnConversations(
        type='direct',
        relation_type='social',
        participant_a_id=participant_a,
        participant_b_id=participant_b,
        participant_a_type='agent' if participant_a.startswith('a_') else 'human',
        participant_b_type='agent' if participant_b.startswith('a_') else 'human',
        status='active',
        current_seq=len(messages),
        message_count=len(messages),
    )
    session.add(conversation)
    await session.flush()
    for participant in (participant_a, participant_b):
        session.add(
            HasnConversationMemberships(
                conversation_id=conversation.id,
                member_hasn_id=participant,
                member_star_id='',
                member_name='快照成员',
                member_type='agent' if participant.startswith('a_') else 'human',
                role='member',
                joined_seq=1,
                read_seq=0,
                state='active',
                history_complete_from_seq=1,
            )
        )
    for sequence, (sender, recipient, text) in enumerate(messages, start=1):
        message = HasnMessages(
            conversation_id=conversation.id,
            conversation_seq=sequence,
            owner_id=None,
            from_id=sender,
            from_type=2 if sender.startswith('a_') else 1,
            to_id=recipient,
            to_type=2 if recipient.startswith('a_') else 1,
            content_type=1,
            content={'text': text},
            process_blocks=[],
            msg_type='message',
            status=1,
            priority='normal',
            local_id=f'snapshot-{uuid.uuid4().hex}',
            mention_all=False,
            origin_node_id='cloud',
        )
        session.add(message)
        await session.flush()
        conversation.last_message_id = message.id
        conversation.last_message_at = message.created_time
        conversation.last_message_preview = text
        conversation.last_message_from = sender
    return str(conversation.id)


async def _cleanup(
    sessionmaker: async_sessionmaker[AsyncSession],
    identities: list[str],
) -> None:
    async with sessionmaker.begin() as session:
        await session.execute(sa.delete(HasnImHistorySnapshots).where(HasnImHistorySnapshots.owner_id.in_(identities)))
        conversation_ids = (
            (
                await session.execute(
                    sa.select(HasnConversationMemberships.conversation_id).where(
                        HasnConversationMemberships.member_hasn_id.in_(identities)
                    )
                )
            )
            .scalars()
            .all()
        )
        if conversation_ids:
            await session.execute(sa.delete(HasnMessages).where(HasnMessages.conversation_id.in_(conversation_ids)))
            await session.execute(
                sa.delete(HasnConversationMemberships).where(
                    HasnConversationMemberships.conversation_id.in_(conversation_ids)
                )
            )
            await session.execute(sa.delete(HasnConversations).where(HasnConversations.id.in_(conversation_ids)))
        await session.execute(sa.delete(HasnAgents).where(HasnAgents.hasn_id.in_(identities)))
        await session.execute(sa.delete(HasnHumans).where(HasnHumans.hasn_id.in_(identities)))


def _message(
    *,
    conversation_id: str,
    conversation_seq: int,
    sender: str,
    recipient: str,
    content: str,
) -> HasnMessages:
    return HasnMessages(
        conversation_id=uuid.UUID(conversation_id),
        conversation_seq=conversation_seq,
        owner_id=None,
        from_id=sender,
        from_type=2 if sender.startswith('a_') else 1,
        to_id=recipient,
        to_type=2 if recipient.startswith('a_') else 1,
        content_type=1,
        content={'text': content},
        process_blocks=[],
        msg_type='message',
        status=1,
        priority='normal',
        local_id=f'snapshot-concurrent-{uuid.uuid4().hex}',
        mention_all=False,
        origin_node_id='cloud',
    )


async def test_snapshot_restores_owner_and_owned_agent_history_with_stable_bound(
    sessionmaker_pg: async_sessionmaker[AsyncSession],
) -> None:
    owner = _human('snapshot_owner_')
    peer = _human('snapshot_peer_')
    other_owner = _human('snapshot_other_')
    owned_agent = _agent('snapshot_agent_')
    disabled_agent = _agent('snapshot_disabled_')
    identities = [owner, peer, other_owner, owned_agent, disabled_agent]
    try:
        async with sessionmaker_pg.begin() as session:
            _seed_humans(session, owner, peer, other_owner)
            marker = uuid.uuid4().hex
            session.add(
                HasnAgents(
                    hasn_id=owned_agent,
                    star_id=f'a{marker[:24]}',
                    owner_id=owner,
                    display_name='快照测试分身',
                    agent_name=f'snapshot{marker[:10]}',
                    api_key_hash=marker,
                    status='active',
                    created_via='client',
                )
            )
            session.add(
                HasnAgents(
                    hasn_id=disabled_agent,
                    star_id=f'd{marker[:24]}',
                    owner_id=owner,
                    display_name='已停用快照测试分身',
                    agent_name=f'disabled{marker[:10]}',
                    api_key_hash=f'disabled{marker}',
                    status='disabled',
                    created_via='client',
                )
            )
            owner_conversation = await _seed_direct_conversation(
                session,
                left=owner,
                right=peer,
                messages=[
                    (owner, peer, '主人消息一'),
                    (peer, owner, '主人消息二'),
                ],
            )
            agent_conversation = await _seed_direct_conversation(
                session,
                left=owned_agent,
                right=peer,
                messages=[
                    (owned_agent, peer, '分身消息一'),
                    (peer, owned_agent, '分身消息二'),
                ],
            )
            unauthorized_conversation = await _seed_direct_conversation(
                session,
                left=other_owner,
                right=peer,
                messages=[(other_owner, peer, '不可见消息')],
            )
            disabled_agent_conversation = await _seed_direct_conversation(
                session,
                left=disabled_agent,
                right=peer,
                messages=[(disabled_agent, peer, '已停用分身消息')],
            )

        async with sessionmaker_pg.begin() as session:
            snapshot = await start_history_snapshot(
                session,
                owner_id=owner,
                head_revision=88,
            )

        async with sessionmaker_pg.begin() as session:
            session.add(
                HasnMessages(
                    conversation_id=uuid.UUID(owner_conversation),
                    conversation_seq=3,
                    owner_id=None,
                    from_id=owner,
                    from_type=1,
                    to_id=peer,
                    to_type=1,
                    content_type=1,
                    content={'text': '快照之后的消息'},
                    process_blocks=[],
                    msg_type='message',
                    status=1,
                    priority='normal',
                    local_id=f'snapshot-after-{uuid.uuid4().hex}',
                    mention_all=False,
                    origin_node_id='cloud',
                )
            )

        async with sessionmaker_pg.begin() as session:
            first_conversations = await list_history_snapshot_conversations(
                session,
                owner_id=owner,
                snapshot_token=snapshot.snapshot_token,
                after=None,
                limit=1,
            )
            second_conversations = await list_history_snapshot_conversations(
                session,
                owner_id=owner,
                snapshot_token=snapshot.snapshot_token,
                after=first_conversations.next_cursor,
                limit=10,
            )
            first_messages = await list_history_snapshot_messages(
                session,
                owner_id=owner,
                snapshot_token=snapshot.snapshot_token,
                after=None,
                limit=2,
            )
            second_messages = await list_history_snapshot_messages(
                session,
                owner_id=owner,
                snapshot_token=snapshot.snapshot_token,
                after=first_messages.next_cursor,
                limit=10,
            )

        conversations = first_conversations.items + second_conversations.items
        messages = first_messages.items + second_messages.items
        assert snapshot.head_revision == 88
        assert snapshot.conversation_count == 3
        assert snapshot.message_count == 5
        assert snapshot.history_complete is True
        assert {item['conversation_id'] for item in conversations} == {
            owner_conversation,
            agent_conversation,
            disabled_agent_conversation,
        }
        assert unauthorized_conversation not in {item['conversation_id'] for item in conversations}
        assert len(messages) == 5
        assert {item['content_body']['text'] for item in messages} == {
            '主人消息一',
            '主人消息二',
            '分身消息一',
            '分身消息二',
            '已停用分身消息',
        }
        ownership_by_content = {
            item['content_body']['text']: item['sender_is_owned']
            for item in messages
        }
        assert ownership_by_content == {
            '主人消息一': True,
            '主人消息二': False,
            '分身消息一': True,
            '分身消息二': False,
            '已停用分身消息': True,
        }
        assert all(item['history_complete'] is True for item in conversations)
        unread_by_conversation = {
            item['conversation_id']: item['unread_count']
            for item in conversations
        }
        assert unread_by_conversation == {
            owner_conversation: 1,
            agent_conversation: 1,
            disabled_agent_conversation: 0,
        }
        upper_bound_by_conversation = {
            item['conversation_id']: item['snapshot_message_seq_upper_bound']
            for item in conversations
        }
        assert upper_bound_by_conversation == {
            owner_conversation: 2,
            agent_conversation: 2,
            disabled_agent_conversation: 1,
        }
        assert all(item['read_state'] and item['read_state'][0]['read_seq'] == 0 for item in conversations)
        assert first_conversations.has_more is True
        assert first_messages.has_more is True
    finally:
        await _cleanup(sessionmaker_pg, identities)


async def test_snapshot_token_cannot_cross_owner(
    sessionmaker_pg: async_sessionmaker[AsyncSession],
) -> None:
    owner = _human('snapshot_token_owner_')
    other_owner = _human('snapshot_token_other_')
    try:
        async with sessionmaker_pg.begin() as session:
            _seed_humans(session, owner, other_owner)
        async with sessionmaker_pg.begin() as session:
            snapshot = await start_history_snapshot(
                session,
                owner_id=owner,
                head_revision=1,
            )
            with pytest.raises(HistorySnapshotTokenError):
                await list_history_snapshot_messages(
                    session,
                    owner_id=other_owner,
                    snapshot_token=snapshot.snapshot_token,
                    after=None,
                    limit=10,
                )
    finally:
        await _cleanup(sessionmaker_pg, [owner, other_owner])


async def test_snapshot_freezes_conversation_projection_and_message_content(
    sessionmaker_pg: async_sessionmaker[AsyncSession],
) -> None:
    owner = _human('snapshot_frozen_owner_')
    peer = _human('snapshot_frozen_peer_')
    try:
        async with sessionmaker_pg.begin() as session:
            _seed_humans(session, owner, peer)
            conversation_id = await _seed_direct_conversation(
                session,
                left=owner,
                right=peer,
                messages=[(owner, peer, '快照建立前的正文')],
            )

        async with sessionmaker_pg.begin() as session:
            snapshot = await start_history_snapshot(
                session,
                owner_id=owner,
                head_revision=2,
            )

        async with sessionmaker_pg.begin() as session:
            conversation = await session.get(
                HasnConversations,
                uuid.UUID(conversation_id),
            )
            assert conversation is not None
            conversation.revision = 99
            message = (
                (
                    await session.execute(
                        sa.select(HasnMessages).where(HasnMessages.conversation_id == uuid.UUID(conversation_id))
                    )
                )
                .scalars()
                .one()
            )
            message.content = {'text': '快照建立后的篡改正文'}

        async with sessionmaker_pg.begin() as session:
            conversations = await list_history_snapshot_conversations(
                session,
                owner_id=owner,
                snapshot_token=snapshot.snapshot_token,
                after=None,
                limit=200,
            )
            messages = await list_history_snapshot_messages(
                session,
                owner_id=owner,
                snapshot_token=snapshot.snapshot_token,
                after=None,
                limit=500,
            )

        assert conversations.items[0]['revision'] == 1
        assert messages.items[0]['content_body']['text'] == '快照建立前的正文'
    finally:
        await _cleanup(sessionmaker_pg, [owner, peer])


async def test_snapshot_excludes_lower_message_id_committed_after_capture(
    sessionmaker_pg: async_sessionmaker[AsyncSession],
) -> None:
    owner = _human('late_owner_')
    peer = _human('late_peer_')
    other_peer = _human('late_other_')
    late_session = sessionmaker_pg()
    try:
        async with sessionmaker_pg.begin() as session:
            _seed_humans(session, owner, peer, other_peer)
            late_conversation = await _seed_direct_conversation(
                session,
                left=owner,
                right=peer,
                messages=[(owner, peer, '并发会话基线')],
            )
            higher_conversation = await _seed_direct_conversation(
                session,
                left=owner,
                right=other_peer,
                messages=[],
            )

        await late_session.begin()
        late_message = _message(
            conversation_id=late_conversation,
            conversation_seq=2,
            sender=owner,
            recipient=peer,
            content='快照之后才提交的较小 ID',
        )
        late_session.add(late_message)
        await late_session.flush()
        late_message_id = int(late_message.id)

        async with sessionmaker_pg.begin() as session:
            higher_message = _message(
                conversation_id=higher_conversation,
                conversation_seq=1,
                sender=owner,
                recipient=other_peer,
                content='快照前已提交的较大 ID',
            )
            session.add(higher_message)
            await session.flush()
            assert int(higher_message.id) > late_message_id

        async with sessionmaker_pg.begin() as session:
            snapshot = await start_history_snapshot(
                session,
                owner_id=owner,
                head_revision=5,
            )

        await late_session.commit()

        async with sessionmaker_pg() as session:
            page = await list_history_snapshot_messages(
                session,
                owner_id=owner,
                snapshot_token=snapshot.snapshot_token,
                after=None,
                limit=500,
            )
            conversations = await list_history_snapshot_conversations(
                session,
                owner_id=owner,
                snapshot_token=snapshot.snapshot_token,
                after=None,
                limit=500,
            )

        contents = {item['content_body']['text'] for item in page.items}
        assert '快照前已提交的较大 ID' in contents
        assert '快照之后才提交的较小 ID' not in contents
        delayed_conversation = next(
            item
            for item in conversations.items
            if item['conversation_id'] == late_conversation
        )
        assert delayed_conversation['snapshot_message_seq_upper_bound'] == 1
    finally:
        if late_session.in_transaction():
            await late_session.rollback()
        await late_session.close()
        await _cleanup(sessionmaker_pg, [owner, peer, other_peer])


async def test_snapshot_rejects_page_after_owned_agent_is_transferred(
    sessionmaker_pg: async_sessionmaker[AsyncSession],
) -> None:
    owner = _human('snapshot_revoke_owner_')
    other_owner = _human('snapshot_revoke_other_')
    peer = _human('snapshot_revoke_peer_')
    owned_agent = _agent('snapshot_revoke_agent_')
    identities = [owner, other_owner, peer, owned_agent]
    try:
        async with sessionmaker_pg.begin() as session:
            _seed_humans(session, owner, other_owner, peer)
            marker = uuid.uuid4().hex
            session.add(
                HasnAgents(
                    hasn_id=owned_agent,
                    star_id=f'a{marker[:24]}',
                    owner_id=owner,
                    display_name='即将转移的分身',
                    agent_name=f'snapshot{marker[:10]}',
                    api_key_hash=marker,
                    status='active',
                    created_via='client',
                )
            )
            await _seed_direct_conversation(
                session,
                left=owned_agent,
                right=peer,
                messages=[(owned_agent, peer, '分身私密历史')],
            )

        async with sessionmaker_pg.begin() as session:
            snapshot = await start_history_snapshot(
                session,
                owner_id=owner,
                head_revision=3,
            )

        async with sessionmaker_pg.begin() as session:
            agent = (
                (await session.execute(sa.select(HasnAgents).where(HasnAgents.hasn_id == owned_agent))).scalars().one()
            )
            agent.owner_id = other_owner

        async with sessionmaker_pg() as session:
            with pytest.raises(
                HistorySnapshotTokenError,
                match='身份归属已变化',
            ):
                await list_history_snapshot_messages(
                    session,
                    owner_id=owner,
                    snapshot_token=snapshot.snapshot_token,
                    after=None,
                    limit=500,
                )
    finally:
        await _cleanup(sessionmaker_pg, identities)


async def test_snapshot_keeps_disbanded_conversation_history(
    sessionmaker_pg: async_sessionmaker[AsyncSession],
) -> None:
    owner = _human('disband_owner_')
    peer = _human('disband_peer_')
    try:
        async with sessionmaker_pg.begin() as session:
            _seed_humans(session, owner, peer)
            conversation_id = await _seed_direct_conversation(
                session,
                left=owner,
                right=peer,
                messages=[(owner, peer, '解散前仍应恢复的历史')],
            )
            conversation = await session.get(
                HasnConversations,
                uuid.UUID(conversation_id),
            )
            assert conversation is not None
            conversation.status = 'disbanded'
            memberships = (
                (
                    await session.execute(
                        sa.select(HasnConversationMemberships).where(
                            HasnConversationMemberships.conversation_id == uuid.UUID(conversation_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
            for membership in memberships:
                membership.left_seq = 1
                membership.state = 'left'

        async with sessionmaker_pg.begin() as session:
            snapshot = await start_history_snapshot(
                session,
                owner_id=owner,
                head_revision=4,
            )
            conversations = await list_history_snapshot_conversations(
                session,
                owner_id=owner,
                snapshot_token=snapshot.snapshot_token,
                after=None,
                limit=200,
            )
            messages = await list_history_snapshot_messages(
                session,
                owner_id=owner,
                snapshot_token=snapshot.snapshot_token,
                after=None,
                limit=500,
            )

        assert [item['conversation_id'] for item in conversations.items] == [conversation_id]
        assert [item['content_body']['text'] for item in messages.items] == ['解散前仍应恢复的历史']
    finally:
        await _cleanup(sessionmaker_pg, [owner, peer])

"""消息提交到 Sync 投影的真实 PostgreSQL 权威链测试。"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnAgents, HasnHumans
from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.app.hasn_im.consumers.framework import ConsumerRunner
from backend.app.hasn_im.consumers.sync_projector import SyncProjector
from backend.app.hasn_im.ports.dto import (
    ActorKind,
    DeliveryState,
    EnsureDirectConversationCommand,
    RecallMessageCommand,
    SendMessageCommand,
    ServicePrincipal,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.schema_names import SCHEMA_NAMES


pytestmark = pytest.mark.asyncio
_EVENTS = SCHEMA_NAMES.im_event_table('integration_events')
_OFFSETS = SCHEMA_NAMES.im_event_table('event_consumer_offsets')
_FAILURES = SCHEMA_NAMES.im_event_table('event_consumer_failures')
_SYNC_EVENTS = SCHEMA_NAMES.sync_table('hasn_sync_events')


@pytest_asyncio.fixture
async def pipeline_sessions():
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
        pytest.fail(f'PostgreSQL 不可达：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_message_commit_then_durable_sync_projection(
    pipeline_sessions,
) -> None:
    """发送和撤回均先落事实，再由 durable projector 后置生成 Sync 事件。"""
    marker = uuid.uuid4().hex
    owner_id = f'h_pipe_{marker[:18]}'
    agent_id = f'a_pipe_{marker[:18]}'
    local_id = f'local_pipe_{marker[:20]}'
    consumer_name = f'sync_projector_pipe_{marker[:12]}'
    conversation_id: str | None = None
    message_id: int | None = None

    async with pipeline_sessions.begin() as db:
        db.add(
            HasnHumans(
                hasn_id=owner_id,
                star_id=f'h{marker[:24]}',
                user_id=int(marker[:15], 16),
                nickname='消息管线主人',
                status='active',
            )
        )
        db.add(
            HasnAgents(
                hasn_id=agent_id,
                star_id=f'a{marker[:24]}',
                owner_id=owner_id,
                display_name='消息管线分身',
                agent_name=f'pipe{marker[:12]}',
                api_key_hash=marker,
                status='active',
                created_via='client',
            )
        )
        head = (
            await db.execute(
                sa.text(
                    f'SELECT COALESCE(MAX(event_seq), 0) FROM {_EVENTS} '
                    'WHERE shard_key = 0'
                )
            )
        ).scalar_one()
        await db.execute(
            sa.text(
                f'INSERT INTO {_OFFSETS} '
                '(consumer_name, last_acked_seq, lease_owner, lease_until, updated_at) '
                'VALUES (:name, :head, NULL, NULL, now())'
            ),
            {'name': consumer_name, 'head': int(head)},
        )

    gateway = PythonLocalImGateway(session_factory=pipeline_sessions)
    principal = ServicePrincipal(
        canonical_sender=owner_id,
        actor_kind=ActorKind.HUMAN,
        origin_node_id='node-pipeline-pg',
    )
    try:
        reference = await gateway.ensure_direct_conversation(
            EnsureDirectConversationCommand(peer_hasn_id=agent_id),
            principal,
        )
        conversation_id = reference.conversation_id
        result = await gateway.send_message(
            SendMessageCommand(
                conversation_id=conversation_id,
                content={'text': '真实消息权威链'},
                idempotency_key=local_id,
            ),
            principal,
        )
        message_id = result.message_id
        assert result.delivery_state is DeliveryState.ACCEPTED
        assert message_id is not None

        async with pipeline_sessions() as db:
            committed = (
                await db.execute(
                    sa.text(
                        f'SELECT event_seq, event_id, payload FROM {_EVENTS} '
                        "WHERE aggregate_id = :conversation_id "
                        "AND event_type = 'im.message.committed.v1'"
                    ),
                    {'conversation_id': conversation_id},
                )
            ).mappings().all()
            projected_before = (
                await db.execute(
                    sa.text(
                        f'SELECT count(*) FROM {_SYNC_EVENTS} '
                        'WHERE owner_id = :owner_id '
                        "AND event_type = 'message.new' "
                        'AND aggregate_id = :message_id'
                    ),
                    {'owner_id': owner_id, 'message_id': str(message_id)},
                )
            ).scalar_one()
        assert len(committed) == 1
        assert committed[0]['payload']['message_id'] == str(message_id)
        assert int(projected_before) == 0

        runner = ConsumerRunner(
            consumer=SyncProjector(consumer_name=consumer_name),
            sessionmaker=pipeline_sessions,
            instance_id=f'worker-{marker[:12]}',
        )
        stats = await runner.tick(batch_limit=20)
        assert stats.processed == 1
        assert not stats.parked

        async with pipeline_sessions() as db:
            projected = (
                await db.execute(
                    sa.text(
                        f'SELECT owner_id, payload FROM {_SYNC_EVENTS} '
                        "WHERE event_type = 'message.new' "
                        'AND aggregate_id = :message_id'
                    ),
                    {'message_id': str(message_id)},
                )
            ).mappings().all()
            committed_count = (
                await db.execute(
                    sa.text(
                        f'SELECT count(*) FROM {_EVENTS} '
                        "WHERE aggregate_id = :conversation_id "
                        "AND event_type = 'im.message.committed.v1'"
                    ),
                    {'conversation_id': conversation_id},
                )
            ).scalar_one()
        assert int(committed_count) == 1
        assert len(projected) == 1
        assert projected[0]['owner_id'] == owner_id
        assert projected[0]['payload']['message_id'] == str(message_id)

        recalled = await gateway.recall_message(
            RecallMessageCommand(
                conversation_id=conversation_id,
                message_id=message_id,
            ),
            principal,
        )
        assert recalled.delivery_state is DeliveryState.ACCEPTED
        recall_stats = await runner.tick(batch_limit=20)
        assert recall_stats.processed == 1
        assert not recall_stats.parked

        async with pipeline_sessions() as db:
            recall_projection = (
                await db.execute(
                    sa.text(
                        f'SELECT owner_id, payload, producer, source_event_id '
                        f'FROM {_SYNC_EVENTS} '
                        "WHERE event_type = 'message.recalled' "
                        'AND aggregate_id = :message_id'
                    ),
                    {'message_id': str(message_id)},
                )
            ).mappings().all()
            recall_fact = (
                await db.execute(
                    sa.text(
                        f'SELECT event_id FROM {_EVENTS} '
                        "WHERE aggregate_id = :conversation_id "
                        "AND event_type = 'im.message.recalled.v1'"
                    ),
                    {'conversation_id': conversation_id},
                )
            ).mappings().one()
        assert len(recall_projection) == 1
        assert recall_projection[0]['owner_id'] == owner_id
        assert recall_projection[0]['payload']['conversation_id'] == conversation_id
        assert recall_projection[0]['payload']['message_id'] == str(message_id)
        assert recall_projection[0]['producer'] == 'hasn_im'
        assert recall_projection[0]['source_event_id'] == recall_fact['event_id']
    finally:
        async with pipeline_sessions.begin() as db:
            if message_id is not None:
                await db.execute(
                    sa.text(
                        f'DELETE FROM {_SYNC_EVENTS} '
                        'WHERE aggregate_id = :message_id'
                    ),
                    {'message_id': str(message_id)},
                )
            await db.execute(
                sa.text(
                    f'DELETE FROM {_FAILURES} WHERE consumer_name = :name'
                ),
                {'name': consumer_name},
            )
            await db.execute(
                sa.text(
                    f'DELETE FROM {_OFFSETS} WHERE consumer_name = :name'
                ),
                {'name': consumer_name},
            )
            if conversation_id is not None:
                await db.execute(
                    sa.text(
                        f'DELETE FROM {_EVENTS} '
                        'WHERE aggregate_id = :conversation_id'
                    ),
                    {'conversation_id': conversation_id},
                )
                await db.execute(
                    sa.text(
                        f'DELETE FROM {SCHEMA_NAMES.im_table("hasn_messages")} '
                        'WHERE conversation_id = CAST(:conversation_id AS uuid)'
                    ),
                    {'conversation_id': conversation_id},
                )
                await db.execute(
                    sa.text(
                        f'DELETE FROM {SCHEMA_NAMES.im_table("hasn_conversation_memberships")} '
                        'WHERE conversation_id = CAST(:conversation_id AS uuid)'
                    ),
                    {'conversation_id': conversation_id},
                )
                await db.execute(
                    sa.text(
                        f'DELETE FROM {SCHEMA_NAMES.im_table("hasn_conversations")} '
                        'WHERE id = CAST(:conversation_id AS uuid)'
                    ),
                    {'conversation_id': conversation_id},
                )
            await db.execute(
                sa.delete(HasnAgents).where(HasnAgents.hasn_id == agent_id)
            )
            await db.execute(
                sa.delete(HasnHumans).where(HasnHumans.hasn_id == owner_id)
            )


async def test_deterministic_suppression_does_not_write_message_or_take_seq(
    pipeline_sessions,
) -> None:
    """确定性门控只保存待放行命令，不提前制造消息事实或占用会话序号。"""
    marker = uuid.uuid4().hex
    sender_id = f'h_suppress_sender_{marker[:12]}'
    owner_id = f'h_suppress_owner_{marker[:12]}'
    agent_id = f'a_suppress_{marker[:16]}'
    idempotency_key = f'suppress-{marker[:20]}'
    conversation_id: str | None = None

    async with pipeline_sessions.begin() as db:
        db.add_all(
            [
                HasnHumans(
                    hasn_id=sender_id,
                    star_id=f'hs{marker[:22]}',
                    user_id=int(marker[:15], 16),
                    nickname='门控发送者',
                    status='active',
                ),
                HasnHumans(
                    hasn_id=owner_id,
                    star_id=f'ho{marker[:22]}',
                    user_id=int(marker[1:16], 16),
                    nickname='门控接收主人',
                    status='active',
                ),
                HasnAgents(
                    hasn_id=agent_id,
                    star_id=f'as{marker[:22]}',
                    owner_id=owner_id,
                    display_name='门控接收分身',
                    agent_name=f'suppress{marker[:10]}',
                    api_key_hash=marker,
                    status='active',
                    created_via='client',
                ),
            ]
        )

    gateway = PythonLocalImGateway(session_factory=pipeline_sessions)
    principal = ServicePrincipal(
        canonical_sender=sender_id,
        actor_kind=ActorKind.HUMAN,
        origin_node_id='node-suppression-pg',
    )
    try:
        reference = await gateway.ensure_direct_conversation(
            EnsureDirectConversationCommand(peer_hasn_id=agent_id),
            principal,
        )
        conversation_id = reference.conversation_id
        result = await gateway.send_message(
            SendMessageCommand(
                conversation_id=conversation_id,
                content={'text': '先进入主人拦截箱'},
                idempotency_key=idempotency_key,
            ),
            principal,
        )
        assert result.delivery_state is DeliveryState.SUPPRESSED
        assert result.message_id is None

        async with pipeline_sessions() as db:
            message_count = await db.scalar(
                sa.text(
                    f'SELECT count(*) FROM {SCHEMA_NAMES.im_table("hasn_messages")} '
                    'WHERE conversation_id = CAST(:conversation_id AS uuid)'
                ),
                {'conversation_id': conversation_id},
            )
            current_seq = await db.scalar(
                sa.text(
                    f'SELECT current_seq FROM {SCHEMA_NAMES.im_table("hasn_conversations")} '
                    'WHERE id = CAST(:conversation_id AS uuid)'
                ),
                {'conversation_id': conversation_id},
            )
            suppressed = (
                await db.execute(
                    sa.text(
                        f'SELECT id, message_id, sender_hasn_id, command_payload '
                        f'FROM {SCHEMA_NAMES.im_table("hasn_suppressed_messages")} '
                        'WHERE conversation_id = CAST(:conversation_id AS uuid)'
                    ),
                    {'conversation_id': conversation_id},
                )
            ).mappings().one()
        assert int(message_count or 0) == 0
        assert int(current_seq or 0) == 0
        assert suppressed['message_id'] is None
        assert suppressed['sender_hasn_id'] == sender_id
        assert suppressed['command_payload']['content'] == {
            'text': '先进入主人拦截箱'
        }
        listed = await gateway.list_suppressed(
            principal=ServicePrincipal(
                canonical_sender=owner_id,
                actor_kind=ActorKind.HUMAN,
            )
        )
        assert len(listed) == 1
        assert listed[0]['suppressed_id'] == str(suppressed['id'])
        assert listed[0]['message_id'] is None
        assert listed[0]['sender_hasn_id'] == sender_id
        assert listed[0]['message_preview'] == '先进入主人拦截箱'

        released = await gateway.release_suppressed(
            suppressed_id=int(suppressed['id']),
            principal=ServicePrincipal(
                canonical_sender=owner_id,
                actor_kind=ActorKind.HUMAN,
                origin_node_id='node-owner-release-pg',
            ),
        )
        assert released.delivery_state is DeliveryState.ACCEPTED
        assert released.message_id is not None
        assert released.conversation_seq == 1

        async with pipeline_sessions() as db:
            released_row = (
                await db.execute(
                    sa.text(
                        f'SELECT message_id, resolved_at, visible_to_owner '
                        f'FROM {SCHEMA_NAMES.im_table("hasn_suppressed_messages")} '
                        'WHERE id = :suppressed_id'
                    ),
                    {'suppressed_id': int(suppressed['id'])},
                )
            ).mappings().one()
            event_count = await db.scalar(
                sa.text(
                    f'SELECT count(*) FROM {_EVENTS} '
                    "WHERE aggregate_id = :conversation_id "
                    "AND event_type = 'im.message.committed.v1'"
                ),
                {'conversation_id': conversation_id},
            )
        assert released_row['message_id'] == released.message_id
        assert released_row['resolved_at'] is not None
        assert released_row['visible_to_owner'] is False
        assert int(event_count or 0) == 1
    finally:
        async with pipeline_sessions.begin() as db:
            if conversation_id is not None:
                for table in (
                    'hasn_suppressed_messages',
                    'hasn_messages',
                    'hasn_conversation_memberships',
                    'hasn_conversations',
                ):
                    await db.execute(
                        sa.text(
                            f'DELETE FROM {SCHEMA_NAMES.im_table(table)} '
                            'WHERE conversation_id = CAST(:conversation_id AS uuid)'
                            if table != 'hasn_conversations'
                            else (
                                f'DELETE FROM {SCHEMA_NAMES.im_table(table)} '
                                'WHERE id = CAST(:conversation_id AS uuid)'
                            )
                        ),
                        {'conversation_id': conversation_id},
                    )
            await db.execute(
                sa.delete(HasnAgents).where(HasnAgents.hasn_id == agent_id)
            )
            await db.execute(
                sa.delete(HasnHumans).where(
                    HasnHumans.hasn_id.in_([sender_id, owner_id])
                )
            )

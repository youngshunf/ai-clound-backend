"""生产方消息 outbox 的真实 PostgreSQL 故障窗口测试。"""

from __future__ import annotations

import uuid

from datetime import timedelta

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_core import HasnHumans
from backend.app.hasn_im.adapters.sqlalchemy_producer_outbox import (
    ProducerOutboxTable,
    SQLAlchemyProducerOutboxStore,
    build_send_message_command,
    enqueue_send_message,
)
from backend.app.hasn_im.application.local_gateway import PythonLocalImGateway
from backend.app.hasn_im.application.outbox_relay import OutboxRelay
from backend.app.hasn_im.ports.dto import (
    ActorKind,
    EnsureDirectConversationCommand,
    SendMessageCommand,
    ServicePrincipal,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL
from backend.database.schema_names import SCHEMA_NAMES
from backend.utils.timezone import timezone

pytestmark = pytest.mark.asyncio
_CONVERSATIONS = SCHEMA_NAMES.im_table('hasn_conversations')
_MESSAGES = SCHEMA_NAMES.im_table('hasn_messages')
_MEMBERSHIPS = SCHEMA_NAMES.im_table('hasn_conversation_memberships')
_UNREAD = SCHEMA_NAMES.im_table('hasn_unread_projection')
_EVENTS = SCHEMA_NAMES.im_event_table('integration_events')

_TABLE = ProducerOutboxTable(
    schema='public',
    table='hasn_notification_im_command_outbox',
    producer='notification',
)


@pytest_asyncio.fixture
async def producer_outbox_sessions():
    """提供隔离连接的真实 PostgreSQL 会话。"""
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


class _FaultAfterImCommit:
    """故障注入：真实 IM 提交成功后模拟 relay 丢失响应。"""

    def __init__(self, real_gateway: PythonLocalImGateway) -> None:
        self._real_gateway = real_gateway

    async def send_message(self, command, principal):
        await self._real_gateway.send_message(command, principal)
        raise ConnectionError('故障注入：IM 已提交但 relay 未收到响应')


async def _delete_case(sessionmaker, *, marker: str, recipient_id: str) -> None:
    """只清理本用例创建的 outbox、消息、会话和身份。"""
    async with sessionmaker.begin() as db:
        await db.execute(
            sa.text(
                'DELETE FROM public.hasn_notification_im_command_outbox '
                'WHERE idempotency_key LIKE :prefix'
            ),
            {'prefix': f'notification:{marker}:%'},
        )
        conversation_ids = list(
            (
                await db.execute(
                    sa.text(
                        f'SELECT id FROM {_CONVERSATIONS} '
                        'WHERE participant_a_id = :sender OR participant_b_id = :recipient'
                    ),
                    {
                        'sender': f'sv_notification_{marker[:12]}',
                        'recipient': recipient_id,
                    },
                )
            )
            .scalars()
            .all()
        )
        if conversation_ids:
            await db.execute(
                sa.text(
                    f'DELETE FROM {_EVENTS} '
                    'WHERE aggregate_type = :aggregate_type '
                    'AND aggregate_id IN ('
                    f'SELECT id::text FROM {_MESSAGES} '
                    'WHERE conversation_id = ANY(:conversation_ids)'
                    ')'
                ),
                {
                    'aggregate_type': 'message',
                    'conversation_ids': conversation_ids,
                },
            )
            await db.execute(
                sa.text(
                    f'DELETE FROM {_UNREAD} '
                    'WHERE conversation_id = ANY(:conversation_ids)'
                ),
                {'conversation_ids': conversation_ids},
            )
            await db.execute(
                sa.text(
                    f'DELETE FROM {_MESSAGES} '
                    'WHERE conversation_id = ANY(:conversation_ids)'
                ),
                {'conversation_ids': conversation_ids},
            )
            await db.execute(
                sa.text(
                    f'DELETE FROM {_MEMBERSHIPS} '
                    'WHERE conversation_id = ANY(:conversation_ids)'
                ),
                {'conversation_ids': conversation_ids},
            )
            await db.execute(
                sa.text(
                    f'DELETE FROM {_CONVERSATIONS} '
                    'WHERE id = ANY(:conversation_ids)'
                ),
                {'conversation_ids': conversation_ids},
            )
        await db.execute(
            sa.delete(HasnHumans).where(HasnHumans.hasn_id == recipient_id)
        )


async def test_transaction_rollback_and_response_loss_recover_once(
    producer_outbox_sessions,
) -> None:
    """业务回滚无命令；IM 响应丢失后同键重试只产生一条消息。"""
    marker = uuid.uuid4().hex
    recipient_id = f'h_pout_{marker[:16]}'
    sender_id = f'sv_notification_{marker[:12]}'
    principal = ServicePrincipal(
        canonical_sender=sender_id,
        actor_kind=ActorKind.SYSTEM_SERVICE,
    )
    gateway = PythonLocalImGateway(session_factory=producer_outbox_sessions)
    store = SQLAlchemyProducerOutboxStore(
        table=_TABLE,
        session_factory=producer_outbox_sessions,
        instance_id=f'test-{marker}',
    )

    try:
        async with producer_outbox_sessions.begin() as db:
            db.add(
                HasnHumans(
                    hasn_id=recipient_id,
                    star_id=f'h{marker[:24]}',
                    user_id=int(marker[:15], 16),
                    nickname=f'生产 outbox 测试主人{marker[:8]}',
                    status='active',
                )
            )

        conversation = await gateway.ensure_direct_conversation(
            EnsureDirectConversationCommand(
                peer_hasn_id=recipient_id,
                relation_type='service',
            ),
            principal,
        )
        command = SendMessageCommand(
            conversation_id=conversation.conversation_id,
            content={'schema_version': 'hasn.card/0.1', 'title': '真实通知卡'},
            content_type=5,
            idempotency_key=f'notification:{marker}:card',
            msg_type='notification',
            context={'notification_id': marker},
        )

        async with producer_outbox_sessions() as db:
            transaction = await db.begin()
            await enqueue_send_message(
                db,
                table=_TABLE,
                command=command,
                principal=principal,
            )
            await transaction.rollback()

        async with producer_outbox_sessions() as db:
            rolled_back = await db.scalar(
                sa.text(
                    'SELECT count(*) FROM public.hasn_notification_im_command_outbox '
                    'WHERE idempotency_key = :key'
                ),
                {'key': command.idempotency_key},
            )
            assert rolled_back == 0

        async with producer_outbox_sessions.begin() as db:
            command_id = await enqueue_send_message(
                db,
                table=_TABLE,
                command=command,
                principal=principal,
            )

        first_relay = OutboxRelay(
            store=store,
            gateway=_FaultAfterImCommit(gateway),
            build_command=build_send_message_command,
            producer='notification',
            backoff_schedule=(1,),
        )
        first = await first_relay.drain_once(
            now=int((timezone.now() + timedelta(seconds=1)).timestamp())
        )
        assert first.claimed >= 1
        assert first.retried >= 1

        async with producer_outbox_sessions() as db:
            own_retry = (
                await db.execute(
                    sa.text(
                        'SELECT status, attempt_count '
                        'FROM public.hasn_notification_im_command_outbox '
                        'WHERE command_id = :command_id'
                    ),
                    {'command_id': command_id},
                )
            ).one()
            assert own_retry.status == 'pending'
            assert own_retry.attempt_count == 1

        recovered_relay = OutboxRelay(
            store=store,
            gateway=gateway,
            build_command=build_send_message_command,
            producer='notification',
            backoff_schedule=(1,),
        )
        recovered = await recovered_relay.drain_once(
            now=int((timezone.now() + timedelta(seconds=2)).timestamp())
        )
        assert recovered.claimed >= 1
        assert recovered.completed >= 1
        assert recovered.deduped >= 1

        async with producer_outbox_sessions() as db:
            row = (
                await db.execute(
                    sa.text(
                        'SELECT status, attempt_count, message_id '
                        'FROM public.hasn_notification_im_command_outbox '
                        'WHERE command_id = :command_id'
                    ),
                    {'command_id': command_id},
                )
            ).one()
            assert row.status == 'completed'
            assert row.attempt_count == 1
            assert row.message_id is not None
            message_count = await db.scalar(
                sa.text(
                    f'SELECT count(*) FROM {_MESSAGES} '
                    'WHERE conversation_id = :conversation_id '
                    'AND local_id = :idempotency_key'
                ),
                {
                    'conversation_id': uuid.UUID(conversation.conversation_id),
                    'idempotency_key': command.idempotency_key,
                },
            )
            assert message_count == 1
    finally:
        await _delete_case(
            producer_outbox_sessions,
            marker=marker,
            recipient_id=recipient_id,
        )


async def test_payload_conflict_and_dead_letter_are_explicit(
    producer_outbox_sessions,
) -> None:
    """同键异 payload 显式冲突；损坏命令达到上限后进入 dead letter。"""
    marker = uuid.uuid4().hex
    key = f'notification:{marker}:conflict'
    principal = ServicePrincipal(
        canonical_sender=f'sv_notification_{marker[:12]}',
        actor_kind=ActorKind.SYSTEM_SERVICE,
    )
    first = SendMessageCommand(
        conversation_id=str(uuid.uuid4()),
        content={'text': '第一版'},
        idempotency_key=key,
    )
    second = SendMessageCommand(
        conversation_id=first.conversation_id,
        content={'text': '不同内容'},
        idempotency_key=key,
    )
    store = SQLAlchemyProducerOutboxStore(
        table=_TABLE,
        session_factory=producer_outbox_sessions,
        instance_id=f'test-{marker}',
    )

    try:
        async with producer_outbox_sessions.begin() as db:
            await enqueue_send_message(
                db,
                table=_TABLE,
                command=first,
                principal=principal,
            )
            with pytest.raises(ValueError, match='幂等键冲突'):
                await enqueue_send_message(
                    db,
                    table=_TABLE,
                    command=second,
                    principal=principal,
                )

        async with producer_outbox_sessions.begin() as db:
            await db.execute(
                sa.text(
                    'UPDATE public.hasn_notification_im_command_outbox '
                    "SET payload = '{}'::jsonb, payload_hash = repeat('0', 64) "
                    'WHERE idempotency_key = :key'
                ),
                {'key': key},
            )

        relay = OutboxRelay(
            store=store,
            gateway=PythonLocalImGateway(
                session_factory=producer_outbox_sessions
            ),
            build_command=build_send_message_command,
            producer='notification',
            max_attempts=1,
        )
        stats = await relay.drain_once(
            now=int((timezone.now() + timedelta(seconds=1)).timestamp())
        )
        assert stats.dead_lettered >= 1

        async with producer_outbox_sessions() as db:
            status = await db.scalar(
                sa.text(
                    'SELECT status FROM public.hasn_notification_im_command_outbox '
                    'WHERE idempotency_key = :key'
                ),
                {'key': key},
            )
            assert status == 'dead_letter'
    finally:
        async with producer_outbox_sessions.begin() as db:
            await db.execute(
                sa.text(
                    'DELETE FROM public.hasn_notification_im_command_outbox '
                    'WHERE idempotency_key LIKE :prefix'
                ),
                {'prefix': f'notification:{marker}:%'},
            )

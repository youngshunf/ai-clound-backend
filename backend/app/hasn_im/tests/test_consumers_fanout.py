"""R2-06 验收：四消费者受众扇出（真实 PG 会话·零 mock·doc16 §7.3）。

三个已上线消费者（audit_projector 后置 R4）各自消费同一条 ``im.message.committed`` 事件，把它
扇出成不同投递。本套钉死**投影时刻重算受众 + origin_session_id 受众分叉 + 收编不变式**：

1. **sync_projector（durable）**：受众每 owner 各一条 ``message.new`` owner feed（经 SyncAppender port）；
   ``origin_session_id`` 只在发送方 owner 的那份携带，其余剥除。
2. **realtime_notifier（best-effort）**：受众每 owner 一帧 ``hasn.message.new``（经 RealtimeGateway port）；
   同样的 origin_session_id 受众分叉。
3. **push_notifier（best-effort）**：**剔除发送方**后每 owner 一次 U-Push；1 秒会话合并去重、payload 不带正文。

受众计算走**真实** ``conversation_projection``（读真库会话行），只对外部 IO 接缝（SyncAppender/
RealtimeGateway/redis/dispatch）注入 port 替身——port 本就为可替换而设，非 mock 业务逻辑。
直接驱动 ``handle``（框架的 cursor/lease/分道语义由 test_consumer_framework 覆盖，此处专测扇出逻辑）。

PG 不可达跳过；每用例 uuid 派生独立 owner/会话/消息，末尾清理自身行。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model import HasnConversationMemberships, HasnConversations
from backend.app.hasn_im.consumers.base import IntegrationEvent
from backend.app.hasn_im.consumers.facts import (
    IM_CONVERSATION_UPDATED,
    IM_MESSAGE_COMMITTED,
    IM_MESSAGE_RECALLED,
)
from backend.app.hasn_im.consumers.push_notifier import PushNotifier
from backend.app.hasn_im.consumers.realtime_notifier import RealtimeNotifier
from backend.app.hasn_im.consumers.sync_projector import SyncProjector
from backend.app.hasn_im.ports.realtime_gateway import RealtimeFrame
from backend.app.hasn_sync.ports.dto import SyncEnvelope, SyncEventRef
from backend.app.services.push_dispatcher import DispatchResult
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
        pytest.skip(f'PostgreSQL 不可达，跳过 R2-06 消费者扇出测试：{exc}')
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


# ---------- port 替身（外部 IO 接缝·非业务 mock） ----------


class _RecordingAppender:
    """SyncAppender 替身：记录扇出的 owner feed envelope，不真写 sync 表。"""

    def __init__(self) -> None:
        self.envelopes: list[SyncEnvelope] = []

    async def append(self, db, envelope: SyncEnvelope) -> SyncEventRef:  # noqa: ANN001
        self.envelopes.append(envelope)
        return SyncEventRef(
            owner_id=envelope.owner_id,
            revision=len(self.envelopes),
            event_id=f'ev_{len(self.envelopes)}',
            event_type=envelope.event_type,
        )


class _RecordingGateway:
    """RealtimeGateway 替身：记录推给每个 owner 的帧。"""

    def __init__(self) -> None:
        self.frames: list[tuple[str, RealtimeFrame]] = []

    async def push_to_owner(self, owner_id: str, frame: RealtimeFrame) -> None:
        self.frames.append((owner_id, frame))

    async def push_to_node(self, node_id: str, frame: RealtimeFrame) -> None:
        raise AssertionError('realtime_notifier 不应调用 node 级投递')


class _FakeRedis:
    """Redis 替身：SET NX EX 语义——acquired 决定是否抢到 1s 会话去重锁。"""

    def __init__(self, *, acquired: bool) -> None:
        self._acquired = acquired
        self.set_calls: list[tuple[str, bool | None, int | None]] = []

    async def set(self, key, value, nx=None, ex=None):  # noqa: ANN001
        self.set_calls.append((key, nx, ex))
        return self._acquired


class _RecordingDispatch:
    """push_dispatcher.dispatch 替身：记录下发目标 + payload。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, db, *, hasn_id, payload):  # noqa: ANN001
        self.calls.append((hasn_id, payload))
        return DispatchResult(sent=1, skipped=0, task_id='t1')


# ---------- 真实会话 seed + 事件构造 ----------


async def _seed_direct_conversation(sm) -> tuple[str, str, str]:
    """建一条 direct 会话（两个人类 owner），返回 (conversation_id, owner_a, owner_b)。

    两方都是 human（h_ 前缀）→ 受众 = {owner_a, owner_b}，无需再 seed agent 行。
    """
    owner_a = f'h_alice_{uuid.uuid4().hex[:10]}'
    owner_b = f'h_bob_{uuid.uuid4().hex[:10]}'
    async with sm() as db:
        conv = HasnConversations(
            type='direct',
            participant_a_id=owner_a,
            participant_a_type='human',
            participant_b_id=owner_b,
            participant_b_type='human',
            status='active',
        )
        db.add(conv)
        await db.flush()
        conv_id = str(conv.id)
        await db.commit()
    return conv_id, owner_a, owner_b


async def _seed_group_conversation(sm) -> tuple[str, str, str]:
    """建立两个成员的群聊，成员可见下界均为第一条消息。"""
    owner_a = f'h_group_alice_{uuid.uuid4().hex[:10]}'
    owner_b = f'h_group_bob_{uuid.uuid4().hex[:10]}'
    async with sm() as db:
        conversation = HasnConversations(
            type='group',
            group_name='事件时受众测试群',
            group_owner_id=owner_a,
            status='active',
            current_seq=2,
        )
        db.add(conversation)
        await db.flush()
        for owner_id in (owner_a, owner_b):
            db.add(
                HasnConversationMemberships(
                    conversation_id=conversation.id,
                    member_hasn_id=owner_id,
                    member_star_id='',
                    member_name='事件时受众测试成员',
                    member_type='human',
                    role='owner' if owner_id == owner_a else 'member',
                    joined_seq=1,
                    read_seq=0,
                    state='active',
                    history_complete_from_seq=1,
                )
            )
        conversation_id = str(conversation.id)
        await db.commit()
    return conversation_id, owner_a, owner_b


def _committed_event(conv_id: str, sender: str, *, origin_session_id: str | None) -> IntegrationEvent:
    """构造一条 im.message.committed 集成事件（payload 携带一条消息的事实）。"""
    message_id = f'msg_{uuid.uuid4().hex[:16]}'
    payload = {
        'conversation_id': conv_id,
        'message_id': message_id,
        'sender_hasn_id': sender,
        'content_type': 1,
        'content_body': {'text': '你好'},
        'origin_node_id': 'node_test',
        'local_id': f'local_{uuid.uuid4().hex[:8]}',
        'created_at': 1_700_000_000,
    }
    if origin_session_id:
        payload['origin_session_id'] = origin_session_id
    return IntegrationEvent(
        event_seq=1,
        event_id=f'ie_{uuid.uuid4().hex[:20]}',
        event_type=IM_MESSAGE_COMMITTED,
        aggregate_type='conversation',
        aggregate_id=conv_id,
        payload=payload,
    )


def _recalled_event(conv_id: str) -> IntegrationEvent:
    """构造一条消息撤回事实事件。"""
    return IntegrationEvent(
        event_seq=2,
        event_id=f'ie_{uuid.uuid4().hex[:20]}',
        event_type=IM_MESSAGE_RECALLED,
        aggregate_type='conversation',
        aggregate_id=conv_id,
        payload={
            'conversation_id': conv_id,
            'message_id': f'msg_{uuid.uuid4().hex[:16]}',
            'conversation_seq': 2,
            'recalled_by': f'h_actor_{uuid.uuid4().hex[:10]}',
            'recalled_at': 1_700_000_100,
        },
    )


def _conversation_updated_event(
    conv_id: str,
    *,
    audience_hasn_ids: list[str],
) -> IntegrationEvent:
    """构造一条含变更前后受众的会话更新事实事件。"""
    return IntegrationEvent(
        event_seq=3,
        event_id=f'ie_{uuid.uuid4().hex[:20]}',
        event_type=IM_CONVERSATION_UPDATED,
        aggregate_type='conversation',
        aggregate_id=conv_id,
        payload={
            'conversation_id': conv_id,
            'revision': 3,
            'change': 'members',
            'added_member_hasn_ids': [],
            'removed_member_hasn_ids': [],
            'audience_hasn_ids': audience_hasn_ids,
            'updated_by': audience_hasn_ids[0],
        },
    )


async def _cleanup(sm, conv_id: str) -> None:
    async with sm() as db:
        await db.execute(sa.text('DELETE FROM hasn_conversations WHERE id = :cid'), {'cid': conv_id})
        await db.commit()


# ---------- 1) sync_projector：受众扇出 message.new + origin_session_id 分叉 ----------


async def test_sync_projector_fans_out_message_new_per_owner(sessionmaker_pg) -> None:
    conv_id, owner_a, owner_b = await _seed_direct_conversation(sessionmaker_pg)
    event = _committed_event(conv_id, sender=owner_a, origin_session_id='sess_a1')
    message_id = event.payload['message_id']

    appender = _RecordingAppender()
    projector = SyncProjector(appender=appender)
    async with sessionmaker_pg() as db:
        await projector.handle(event, db)
        await db.commit()

    # 受众两个 owner 各一条 message.new（aggregate_id = message_id）
    mine = [e for e in appender.envelopes if e.aggregate_id == message_id]
    assert {e.owner_id for e in mine} == {owner_a, owner_b}
    assert all(e.event_type == 'message.new' and e.aggregate_type == 'message' for e in mine)

    # origin_session_id 只在发送方 owner_a 的那份携带，owner_b 一律剥除（doc14 §6.2）
    by_owner = {e.owner_id: e for e in mine}
    assert by_owner[owner_a].payload.get('origin_session_id') == 'sess_a1'
    assert 'origin_session_id' not in by_owner[owner_b].payload
    # 瘦事件字段齐全（content_type 转 MIME）
    assert by_owner[owner_b].payload['content_type'] == 'text'
    assert by_owner[owner_b].payload['conversation_id'] == conv_id

    await _cleanup(sessionmaker_pg, conv_id)


async def test_sync_projector_projects_recall_and_conversation_update(
    sessionmaker_pg,
) -> None:
    conv_id, owner_a, owner_b = await _seed_direct_conversation(sessionmaker_pg)
    recalled = _recalled_event(conv_id)
    conversation_updated = _conversation_updated_event(
        conv_id,
        audience_hasn_ids=[owner_a, owner_b],
    )
    appender = _RecordingAppender()
    async with sessionmaker_pg() as db:
        projector = SyncProjector(appender=appender)
        await projector.handle(recalled, db)
        await projector.handle(conversation_updated, db)
        await db.commit()

    recalled_rows = [envelope for envelope in appender.envelopes if envelope.event_type == 'message.recalled']
    assert {envelope.owner_id for envelope in recalled_rows} == {
        owner_a,
        owner_b,
    }
    assert all(
        envelope.aggregate_id == recalled.payload['message_id']
        and envelope.aggregate_type == 'message'
        and envelope.source_event_id == recalled.event_id
        and envelope.payload == recalled.payload
        for envelope in recalled_rows
    )

    updated_rows = [envelope for envelope in appender.envelopes if envelope.event_type == 'conversation.updated']
    assert {envelope.owner_id for envelope in updated_rows} == {
        owner_a,
        owner_b,
    }
    assert all(
        envelope.aggregate_id == conv_id
        and envelope.aggregate_type == 'conversation'
        and envelope.source_event_id == conversation_updated.event_id
        and envelope.payload == {'conversation_id': conv_id, 'revision': 3}
        for envelope in updated_rows
    )
    await _cleanup(sessionmaker_pg, conv_id)


async def test_sync_projector_uses_event_time_membership_after_member_leaves(
    sessionmaker_pg,
) -> None:
    """消息提交后成员退出，延迟消费仍必须把新增与撤回投影给该成员主人。"""
    conv_id, owner_a, owner_b = await _seed_group_conversation(sessionmaker_pg)
    committed = _committed_event(
        conv_id,
        sender=owner_a,
        origin_session_id=None,
    )
    committed.payload['conversation_seq'] = 2
    recalled = _recalled_event(conv_id)

    async with sessionmaker_pg.begin() as db:
        membership = (
            (
                await db.execute(
                    sa.select(HasnConversationMemberships).where(
                        HasnConversationMemberships.conversation_id == conv_id,
                        HasnConversationMemberships.member_hasn_id == owner_b,
                    )
                )
            )
            .scalars()
            .one()
        )
        membership.left_seq = 2
        membership.state = 'left'

    appender = _RecordingAppender()
    async with sessionmaker_pg() as db:
        projector = SyncProjector(appender=appender)
        await projector.handle(committed, db)
        await projector.handle(recalled, db)

    assert {envelope.owner_id for envelope in appender.envelopes if envelope.event_type == 'message.new'} == {
        owner_a,
        owner_b,
    }
    assert {envelope.owner_id for envelope in appender.envelopes if envelope.event_type == 'message.recalled'} == {
        owner_a,
        owner_b,
    }
    await _cleanup(sessionmaker_pg, conv_id)


# ---------- 2) realtime_notifier：受众每 owner 一帧 hasn.message.new ----------


async def test_realtime_notifier_pushes_frame_per_owner(sessionmaker_pg) -> None:
    conv_id, owner_a, owner_b = await _seed_direct_conversation(sessionmaker_pg)
    event = _committed_event(conv_id, sender=owner_a, origin_session_id='sess_a1')
    message_id = event.payload['message_id']

    gateway = _RecordingGateway()
    async with sessionmaker_pg() as db:
        await RealtimeNotifier(gateway=gateway).handle(event, db)
        await db.commit()

    mine = [(o, f) for (o, f) in gateway.frames if f.params.get('message_id') == message_id]
    assert {o for (o, _) in mine} == {owner_a, owner_b}
    assert all(f.method == 'hasn.message.new' for (_, f) in mine)

    by_owner = dict(mine)
    assert by_owner[owner_a].params.get('origin_session_id') == 'sess_a1'
    assert 'origin_session_id' not in by_owner[owner_b].params

    await _cleanup(sessionmaker_pg, conv_id)


async def test_realtime_notifier_pushes_invalidations_for_recall_and_conversation_update(
    sessionmaker_pg,
) -> None:
    conv_id, owner_a, owner_b = await _seed_direct_conversation(sessionmaker_pg)
    recalled = _recalled_event(conv_id)
    conversation_updated = _conversation_updated_event(
        conv_id,
        audience_hasn_ids=[owner_a, owner_b],
    )
    gateway = _RecordingGateway()
    async with sessionmaker_pg() as db:
        notifier = RealtimeNotifier(gateway=gateway)
        await notifier.handle(recalled, db)
        await notifier.handle(conversation_updated, db)
        await db.commit()

    recalled_frames = [
        (owner_id, frame) for owner_id, frame in gateway.frames if frame.method == 'hasn.message.invalidated'
    ]
    assert {owner_id for owner_id, _ in recalled_frames} == {
        owner_a,
        owner_b,
    }
    assert all(
        frame.params
        == {
            **recalled.payload,
            'event_id': recalled.event_id,
        }
        for _, frame in recalled_frames
    )

    conversation_frames = [
        (owner_id, frame) for owner_id, frame in gateway.frames if frame.method == 'hasn.conversation.invalidated'
    ]
    assert {owner_id for owner_id, _ in conversation_frames} == {
        owner_a,
        owner_b,
    }
    assert all(
        frame.params
        == {
            'conversation_id': conv_id,
            'revision': 3,
            'event_id': conversation_updated.event_id,
        }
        for _, frame in conversation_frames
    )
    await _cleanup(sessionmaker_pg, conv_id)


# ---------- 3) push_notifier：剔除发送方 + 1s 去重 + 无正文 ----------


async def test_push_notifier_excludes_sender_and_omits_body(sessionmaker_pg) -> None:
    conv_id, owner_a, owner_b = await _seed_direct_conversation(sessionmaker_pg)
    event = _committed_event(conv_id, sender=owner_a, origin_session_id=None)

    redis = _FakeRedis(acquired=True)
    dispatch = _RecordingDispatch()
    async with sessionmaker_pg() as db:
        await PushNotifier(redis=redis, dispatch_fn=dispatch).handle(event, db)
        await db.commit()

    # 发送方 owner_a 被剔除，只推给 owner_b
    assert [hasn_id for (hasn_id, _) in dispatch.calls] == [owner_b]
    # 1s 会话去重锁：SET NX EX=1
    assert redis.set_calls == [(f'hasn_push_dedup:{conv_id}', True, 1)]
    # payload 只带占位 + trace_id，绝不带消息正文（不变式 §4）
    _, payload = dispatch.calls[0]
    assert payload == {'title': '新消息', 'body': '您有一条新消息', 'trace_id': f'conv:{conv_id}'}
    assert 'content_body' not in payload and 'text' not in payload

    await _cleanup(sessionmaker_pg, conv_id)


async def test_push_notifier_dedup_skips_within_window(sessionmaker_pg) -> None:
    conv_id, owner_a, _ = await _seed_direct_conversation(sessionmaker_pg)
    event = _committed_event(conv_id, sender=owner_a, origin_session_id=None)

    # 未抢到锁（1s 窗口内已下发过）→ 整轮扇出合并跳过，不下发
    redis = _FakeRedis(acquired=False)
    dispatch = _RecordingDispatch()
    async with sessionmaker_pg() as db:
        await PushNotifier(redis=redis, dispatch_fn=dispatch).handle(event, db)
        await db.commit()

    assert dispatch.calls == []
    assert len(redis.set_calls) == 1  # 抢锁尝试了一次，失败即返回

    await _cleanup(sessionmaker_pg, conv_id)

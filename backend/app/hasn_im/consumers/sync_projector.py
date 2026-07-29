"""hasn_im.consumers.sync_projector · durable 消费者：受众扇出 message.new（§7.3-1）

消费 ``im.message.committed`` → 计算受众 owner → 对每个 owner 经 ``SyncAppender`` port 追加
一条 ``message.new`` owner feed 事件（与 ``_fanout_message_new`` 的 ``append_message_new_event``
逐字段同构）。durable：``handle`` 内的所有 append 与消费者 cursor 推进由框架**同事务**提交，
失败即整批回滚重试→dead letter；参与 retention 低水位。

**幂等**（§7.3）：框架 durable cursor 保证同一集成事件至多处理一次；R2-07 建成
``hasn_sync.append_event`` PG 函数后，``(producer='hasn_im', source_event_id, owner_id)``
再叠一层跨重启去重（envelope 追加 producer/source_event_id，port 形状不变）——两层同向兜底。

**origin_session_id 受众分叉（doc14 §6.2 隐私红线）**：发起会话溯源是**发送方的执行细节**，
只有 ``audience_owner == sender_owner`` 的那份 feed 事件携带，其余受众一律剥除。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.service import conversation_projection as cp
from backend.app.hasn_im.consumers.audience import (
    resolve_audience_owner_ids,
)
from backend.app.hasn_im.consumers.base import ConsumerClass, IntegrationEvent
from backend.app.hasn_im.consumers.facts import (
    IM_CONVERSATION_UPDATED,
    IM_MESSAGE_COMMITTED,
    IM_MESSAGE_RECALLED,
    ConversationUpdatedFacts,
    MessageCommittedFacts,
    MessageRecalledFacts,
)
from backend.app.hasn_sync.adapters.sqlalchemy_appender import SqlAlchemySyncAppender
from backend.app.hasn_sync.ports.dto import SyncEnvelope
from backend.app.hasn_sync.ports.sync_appender import SyncAppender

_MESSAGE_NEW = 'message.new'
_MESSAGE_RECALLED = 'message.recalled'
_CONVERSATION_UPDATED = 'conversation.updated'
# 幂等去重键的 producer 段（§7.3）——本消费者产的 sync 事件统一标 'hasn_im'。
_PRODUCER = 'hasn_im'


@dataclass(slots=True)
class SyncProjector:
    """durable：把已提交消息扇出成各受众 owner feed 的 message.new（§7.3-1）。"""

    # sync 事件唯一写入口（§3.2）；默认现网薄封装，测试可注入替身。
    appender: SyncAppender = field(default_factory=SqlAlchemySyncAppender)
    consumer_name: str = 'sync_projector'

    @property
    def name(self) -> str:
        return self.consumer_name

    @property
    def consumer_class(self) -> ConsumerClass:
        return ConsumerClass.DURABLE

    async def handle(self, event: IntegrationEvent, db: AsyncSession) -> None:
        """把消息及会话事实投影到每个受众 owner 的权威 feed。"""
        if event.event_type == IM_MESSAGE_COMMITTED:
            await self._project_message_committed(event, db)
        elif event.event_type == IM_MESSAGE_RECALLED:
            await self._project_message_recalled(event, db)
        elif event.event_type == IM_CONVERSATION_UPDATED:
            await self._project_conversation_updated(event, db)

    async def _project_message_committed(
        self,
        event: IntegrationEvent,
        db: AsyncSession,
    ) -> None:
        """扇出一条 message.new 到每个当前受众 owner。"""
        facts = MessageCommittedFacts.from_event(event)
        audience = await resolve_audience_owner_ids(
            db,
            conversation_id=facts.conversation_id,
            conversation_seq=facts.conversation_seq,
        )
        if not audience:
            return

        # 仅当携带溯源时才解析发送方 owner（用于受众分叉；否则省一次查询）。
        sender_owner_id: str | None = None
        if facts.origin_session_id:
            resolved = await cp._resolve_owner_ids(db, [facts.sender_hasn_id])
            sender_owner_id = resolved.get(facts.sender_hasn_id)

        for owner_id in audience:
            owner_origin_session_id = (
                facts.origin_session_id if (facts.origin_session_id and owner_id == sender_owner_id) else None
            )
            envelope = SyncEnvelope(
                owner_id=owner_id,
                hasn_id=owner_id,
                event_type=_MESSAGE_NEW,
                aggregate_type='message',
                aggregate_id=facts.message_id,
                payload=_message_new_payload(facts, owner_origin_session_id),
                # 跨重启第二层去重键（§7.3）：durable cursor 之外再叠 (owner, producer, source_event_id)。
                # 同一集成事件扇出到各 owner，去重键含 owner_id（在 append_event 函数内），各 owner 各落一行。
                producer=_PRODUCER,
                source_event_id=event.event_id,
            )
            await self.appender.append(db, envelope)

    async def _project_message_recalled(
        self,
        event: IntegrationEvent,
        db: AsyncSession,
    ) -> None:
        """把撤回事实持久扇出，供离线设备修正已镜像消息。"""
        facts = MessageRecalledFacts.from_event(event)
        audience = await resolve_audience_owner_ids(
            db,
            conversation_id=facts.conversation_id,
            conversation_seq=facts.conversation_seq,
        )
        for owner_id in audience:
            await self.appender.append(
                db,
                SyncEnvelope(
                    owner_id=owner_id,
                    hasn_id=owner_id,
                    event_type=_MESSAGE_RECALLED,
                    aggregate_type='message',
                    aggregate_id=facts.message_id,
                    payload=facts.payload(),
                    producer=_PRODUCER,
                    source_event_id=event.event_id,
                ),
            )

    async def _project_conversation_updated(
        self,
        event: IntegrationEvent,
        db: AsyncSession,
    ) -> None:
        """把会话 revision 扇出给变更前后全部受众。"""
        facts = ConversationUpdatedFacts.from_event(event)
        audience = await resolve_audience_owner_ids(
            db,
            conversation_id=facts.conversation_id,
            frozen_hasn_ids=facts.audience_hasn_ids,
        )
        for owner_id in audience:
            await self.appender.append(
                db,
                SyncEnvelope(
                    owner_id=owner_id,
                    hasn_id=owner_id,
                    event_type=_CONVERSATION_UPDATED,
                    aggregate_type='conversation',
                    aggregate_id=facts.conversation_id,
                    payload=facts.payload(),
                    producer=_PRODUCER,
                    source_event_id=event.event_id,
                ),
            )


def _message_new_payload(facts: MessageCommittedFacts, origin_session_id: str | None) -> dict[str, Any]:
    """message.new 瘦事件 payload（8 字段 + 条件 origin_session_id，与现网 append_message_new_event 同构）。"""
    payload: dict[str, Any] = {
        'conversation_id': facts.conversation_id,
        'message_id': facts.message_id,
        'sender_hasn_id': facts.sender_hasn_id,
        'origin_node_id': facts.origin_node_id,
        'content_type': cp.content_type_to_mime(facts.content_type),
        'content_body': facts.content_body,
        'local_id': facts.local_id,
        'created_at': int(facts.created_at),
    }
    if origin_session_id:
        payload['origin_session_id'] = origin_session_id
    return payload

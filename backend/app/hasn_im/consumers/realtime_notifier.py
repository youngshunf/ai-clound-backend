"""hasn_im.consumers.realtime_notifier · best-effort 消费者：实时推 message.new（§7.3-2）

消费 ``im.message.committed`` → 计算受众 owner → 对每个 owner 经 ``RealtimeGateway`` 推一帧
``hasn.message.new``（与 ``_fanout_message_new`` 的 deferred push 同构）。best-effort（§7.2）：
帧可重复可丢、**不重试不进 DLQ**，失败由框架记 metric 后仍推进 cursor；不参与 retention。
丢失由 daemon 常驻 sync pull（§8.2）兜底——权威 feed 已由 sync_projector 落库。

``handle`` 收到框架传入的 ``db`` 仅用于读会话/名册算受众（实时推送是外部 IO，不写业务库）。
``origin_session_id`` 受众分叉同 sync_projector（doc14 §6.2）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.service import conversation_projection as cp
from backend.app.hasn_im.adapters.routing.node_session_realtime_gateway import NodeSessionRealtimeGateway
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
from backend.app.hasn_im.ports.realtime_gateway import RealtimeFrame, RealtimeGateway

_METHOD_MESSAGE_NEW = 'hasn.message.new'
_METHOD_MESSAGE_INVALIDATED = 'hasn.message.invalidated'
_METHOD_CONVERSATION_INVALIDATED = 'hasn.conversation.invalidated'


@dataclass(slots=True)
class RealtimeNotifier:
    """best-effort：把已提交消息实时推给每个受众 owner 的在线设备（§7.3-2）。"""

    gateway: RealtimeGateway = field(default_factory=NodeSessionRealtimeGateway)

    @property
    def name(self) -> str:
        return 'realtime_notifier'

    @property
    def consumer_class(self) -> ConsumerClass:
        return ConsumerClass.BEST_EFFORT

    async def handle(self, event: IntegrationEvent, db: AsyncSession) -> None:
        """把新消息或失效信号推给在线受众。"""
        if event.event_type == IM_MESSAGE_COMMITTED:
            await self._push_message_new(event, db)
        elif event.event_type == IM_MESSAGE_RECALLED:
            await self._push_message_invalidated(event, db)
        elif event.event_type == IM_CONVERSATION_UPDATED:
            await self._push_conversation_invalidated(event, db)

    async def _push_message_new(
        self,
        event: IntegrationEvent,
        db: AsyncSession,
    ) -> None:
        """推一帧 hasn.message.new 给每个当前受众 owner。"""
        facts = MessageCommittedFacts.from_event(event)
        audience = await resolve_audience_owner_ids(
            db,
            conversation_id=facts.conversation_id,
        )
        if not audience:
            return

        sender_owner_id: str | None = None
        if facts.origin_session_id:
            resolved = await cp._resolve_owner_ids(db, [facts.sender_hasn_id])
            sender_owner_id = resolved.get(facts.sender_hasn_id)

        for owner_id in audience:
            owner_origin_session_id = (
                facts.origin_session_id
                if (facts.origin_session_id and owner_id == sender_owner_id)
                else None
            )
            frame = RealtimeFrame(
                method=_METHOD_MESSAGE_NEW,
                params=_frame_params(facts, owner_origin_session_id),
            )
            await self.gateway.push_to_owner(owner_id, frame)

    async def _push_message_invalidated(
        self,
        event: IntegrationEvent,
        db: AsyncSession,
    ) -> None:
        """实时通知在线设备撤回既有消息。"""
        facts = MessageRecalledFacts.from_event(event)
        audience = await resolve_audience_owner_ids(
            db,
            conversation_id=facts.conversation_id,
        )
        params = {**facts.payload(), 'event_id': event.event_id}
        for owner_id in audience:
            await self.gateway.push_to_owner(
                owner_id,
                RealtimeFrame(
                    method=_METHOD_MESSAGE_INVALIDATED,
                    params=params,
                ),
            )

    async def _push_conversation_invalidated(
        self,
        event: IntegrationEvent,
        db: AsyncSession,
    ) -> None:
        """实时通知变更前后受众重新读取会话对象。"""
        facts = ConversationUpdatedFacts.from_event(event)
        audience = await resolve_audience_owner_ids(
            db,
            conversation_id=facts.conversation_id,
            frozen_hasn_ids=facts.audience_hasn_ids,
        )
        params = {**facts.payload(), 'event_id': event.event_id}
        for owner_id in audience:
            await self.gateway.push_to_owner(
                owner_id,
                RealtimeFrame(
                    method=_METHOD_CONVERSATION_INVALIDATED,
                    params=params,
                ),
            )


def _frame_params(facts: MessageCommittedFacts, origin_session_id: str | None) -> dict[str, Any]:
    """实时帧 params（与 sync_projector 的 message.new payload 同构，客户端按 message_id 去重）。"""
    params: dict[str, Any] = {
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
        params['origin_session_id'] = origin_session_id
    return params

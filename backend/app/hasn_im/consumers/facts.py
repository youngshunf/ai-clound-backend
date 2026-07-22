"""hasn_im.consumers.facts · im.message.committed 集成事件的事实契约（§7.3）

send 事务 commit 时随消息落库追加**一条** ``im.message.committed.v1`` 集成事件
（R2-04 integration_events），其 payload 携带一条消息投影所需的全部事实。三个消费者
（sync_projector / realtime_notifier / push_notifier）各自独立消费同一条事件、按 event_seq
顺序处理，把它扇出成 owner feed 的 ``message.new`` 同步事件 + 实时推送 + 移动推送。

**为什么消费者在投影时刻重算受众**：受众 = ``⋃ resolve_owner(participant)``（direct 两方 /
group 当前名册）。用**投影时刻**的会话/名册算受众，比冻结 send 时刻的受众更正确（群加人后
新成员也能收到），且避免把易变的 owner 列表塞进事件 payload。故事件只带**消息事实**，
受众由消费者调 ``conversation_projection.compute_audience_owner_ids`` 现算（收编期允许依赖，
与 ``local_gateway`` 依赖 ``message_router`` 同口径）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.app.hasn_im.consumers.base import IntegrationEvent

# send 事务落库时追加的唯一集成事件类型（aggregate_type='conversation'，aggregate_id=会话 id）
IM_MESSAGE_COMMITTED = 'im.message.committed.v1'


@dataclass(frozen=True)
class MessageCommittedFacts:
    """一条已提交消息的事实（从 im.message.committed 事件 payload 解析）。

    字段对齐 ``_fanout_message_new`` 的 ``_params_for``（去掉受众相关的分叉）——受众与
    ``origin_session_id`` 的**发送方 owner 分叉**在消费者内按投影时刻算，事件只带原始事实。
    """

    conversation_id: str
    message_id: str
    sender_hasn_id: str
    content_type: int
    content_body: dict[str, Any]
    conversation_seq: int | None = None
    origin_node_id: str | None = None
    # 发起会话溯源（doc14 §6.2）：仅发送方 owner 的投影携带；消费者按受众分叉剥除。
    origin_session_id: str | None = None
    local_id: str | None = None
    created_at: int = 0

    @classmethod
    def from_event(cls, event: IntegrationEvent) -> MessageCommittedFacts:
        """从集成事件 payload 解析事实（缺字段用安全缺省，避免消费者对畸形事件炸整批）。"""
        p = event.payload or {}
        return cls(
            conversation_id=str(p.get('conversation_id') or event.aggregate_id),
            message_id=str(p.get('message_id') or ''),
            sender_hasn_id=str(p.get('sender_hasn_id') or ''),
            content_type=int(p.get('content_type') or 1),
            content_body=dict(p.get('content_body') or {}),
            conversation_seq=(int(p['conversation_seq']) if p.get('conversation_seq') is not None else None),
            origin_node_id=p.get('origin_node_id'),
            origin_session_id=p.get('origin_session_id'),
            local_id=p.get('local_id'),
            created_at=int(p.get('created_at') or 0),
        )

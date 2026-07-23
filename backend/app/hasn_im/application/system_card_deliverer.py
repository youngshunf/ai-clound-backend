"""hasn_im.application.system_card_deliverer · 系统卡片投递（R1-06 收编）

把「以系统 / 服务身份投一张卡片进 from⇄recipient 会话」这条**第二套落库路径**从
notification 域收编进通信域——原 `notification_carrier._persist_card` 直连
`message_router.persist_message` + 手动 `_append_sync_event`（绕 social 权限矩阵：
主人自己的服务号 / 分身通知对主人天然可见，符合「通信对主人透明」）。§1.1/§1.2-3
把这条旁路点名为**最先要消灭的第二套落库路径**——本函数即其收编落点。

**R1-06 只搬家不改语义**：会话建立 / 卡片落库 / `message.received` sync event 与现网
**逐字节一致**，故 daemon 镜像不受影响、云端 pytest 全验证。收编期仍**收调用方 db、
不自开事务、不自 commit**——notification `emit()` 把「权威通知行 + 卡片 + sync event」
落在单事务里（原子不变量，见 `notification_service.emit`），本函数复用其 db 参与同一
事务；R1-08 事务收口 / R2 消费者化后，卡片投递改为经 `integration_events` →
`sync_projector` 消费者，本函数随之退役。

依赖方向（§0.1）：notification 业务模块经本函数（通信域 application API）投递，
不再直连 `persist_message`；业务层保持零直连。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.app.hasn_im.application.message_service import (
    get_or_create_conversation,
    persist_message,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# content_type=5 卡片（与 notification_carrier._CONTENT_TYPE_CARD 同口径）
_CONTENT_TYPE_CARD = 5

# 有效优先级白名单（越界回落 normal，与现网 _persist_card 一致）
_VALID_PRIORITIES = ('critical', 'high', 'normal', 'low')


async def deliver_system_card(
    db: AsyncSession,
    *,
    recipient_id: str,
    recipient_type: str,
    from_id: str,
    peer_type: str,
    relation_type: str,
    conversation_type: str,
    card_body: dict[str, Any],
    priority: str,
    msg_type: str = 'notification',
    notif_id: int | None = None,
    local_id: str | None = None,
) -> int:
    """以系统身份把卡片投进「from ⇄ recipient」会话，返回消息 id（绕 social 权限：主人自见）。

    逐字节复刻原 `notification_carrier._persist_card`：get_or_create_conversation +
    persist_message + `message.received` sync_event（让接收方节点经 sync/pull 镜像这条卡片
    ——persist_message 直写绕开 route_message，本身不产生同步事件，缺这步则云端落库但 daemon
    永不镜像）。owner 即接收方（recipient_id）。**用调用方 db，不 commit**（参与其事务）。
    """
    from backend.app.hasn.service.hasn_sync_service import SqlAlchemySyncGateway

    conv = await get_or_create_conversation(
        db,
        recipient_id,
        recipient_type,
        from_id,
        peer_type,
        relation_type=relation_type,
    )
    msg = await persist_message(
        db,
        conversation_id=str(conv.id),
        from_id=from_id,
        to_id=recipient_id,
        content=card_body,
        content_type=_CONTENT_TYPE_CARD,
        msg_type=msg_type,
        priority=priority if priority in _VALID_PRIORITIES else 'normal',
        local_id=local_id,
        context={'notification_id': notif_id, 'conversation_type': conversation_type},
    )

    sync_gw = SqlAlchemySyncGateway()
    await sync_gw._append_sync_event(
        db,
        owner_id=recipient_id,
        hasn_id=recipient_id,
        event_type='message.received',
        aggregate_type='message',
        aggregate_id=str(msg.id),
        payload={
            'message_id': str(msg.id),
            'conversation_id': str(conv.id),
            'owner_id': recipient_id,
            'hasn_id': recipient_id,
            'sender_hasn_id': from_id,
            'recipient_hasn_id': recipient_id,
            'direction': 'inbound',
            'content_type': 'application/x.card+json',
            'content_body': card_body,
            'local_id': None,
            'created_at': int(msg.created_time.timestamp()) if msg.created_time else 0,
        },
    )
    return msg.id

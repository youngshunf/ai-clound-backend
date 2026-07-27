"""通知域消息 outbox 的表绑定与公共 relay 装配。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.hasn_im.adapters.sqlalchemy_producer_outbox import (
    ProducerOutboxTable,
    SQLAlchemyProducerOutboxStore,
    build_send_message_command,
)
from backend.app.hasn_im.application.outbox_relay import OutboxRelay
from backend.app.hasn_im.ports.im_gateway import ImGateway

NOTIFICATION_IM_OUTBOX = ProducerOutboxTable(
    schema='public',
    table='hasn_notification_im_command_outbox',
    producer='notification',
)


def build_notification_im_relay(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    gateway: ImGateway,
    instance_id: str,
) -> OutboxRelay:
    """以通知自有表装配统一 relay，不复制领取与重试逻辑。"""
    return OutboxRelay(
        store=SQLAlchemyProducerOutboxStore(
            table=NOTIFICATION_IM_OUTBOX,
            session_factory=session_factory,
            instance_id=instance_id,
        ),
        gateway=gateway,
        build_command=build_send_message_command,
        producer=NOTIFICATION_IM_OUTBOX.producer,
    )

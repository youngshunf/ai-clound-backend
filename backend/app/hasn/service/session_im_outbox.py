"""工作会话与应用完成卡 outbox 的表绑定和 relay 装配。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.hasn_im.adapters.sqlalchemy_producer_outbox import (
    ProducerOutboxTable,
    SQLAlchemyProducerOutboxStore,
    build_send_message_command,
)
from backend.app.hasn_im.application.outbox_relay import OutboxRelay
from backend.app.hasn_im.ports.im_gateway import ImGateway

SESSION_IM_OUTBOX = ProducerOutboxTable(
    schema='public',
    table='hasn_session_im_command_outbox',
    producer='session',
)


def build_session_im_relay(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    gateway: ImGateway,
    instance_id: str,
) -> OutboxRelay:
    """以会话生产方自有表装配统一 relay。"""
    return OutboxRelay(
        store=SQLAlchemyProducerOutboxStore(
            table=SESSION_IM_OUTBOX,
            session_factory=session_factory,
            instance_id=instance_id,
        ),
        gateway=gateway,
        build_command=build_send_message_command,
        producer=SESSION_IM_OUTBOX.producer,
    )

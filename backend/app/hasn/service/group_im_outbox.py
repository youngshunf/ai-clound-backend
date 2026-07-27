"""群业务 IM 命令 outbox 的表绑定和 relay 装配。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.hasn_im.adapters.sqlalchemy_producer_outbox import (
    ProducerOutboxTable,
    SQLAlchemyProducerOutboxStore,
    build_send_message_command,
)
from backend.app.hasn_im.application.outbox_relay import OutboxRelay
from backend.app.hasn_im.ports.im_gateway import ImGateway

GROUP_IM_OUTBOX = ProducerOutboxTable(
    schema='public',
    table='hasn_group_im_command_outbox',
    producer='group',
)


def build_group_im_relay(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    gateway: ImGateway,
    instance_id: str,
) -> OutboxRelay:
    """以群业务生产方自有表装配统一 relay。"""
    return OutboxRelay(
        store=SQLAlchemyProducerOutboxStore(
            table=GROUP_IM_OUTBOX,
            session_factory=session_factory,
            instance_id=instance_id,
        ),
        gateway=gateway,
        build_command=build_send_message_command,
        producer=GROUP_IM_OUTBOX.producer,
    )

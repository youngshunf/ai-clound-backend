from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.crud.crud_pay_channel import pay_channel_dao
from backend.app.billing.model.pay_channel import PayChannel
from backend.app.billing.schema.pay_channel import (
    CreatePayChannelParam,
    DeletePayChannelParam,
    UpdatePayChannelParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class PayChannelService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> PayChannel:
        channel = await pay_channel_dao.get(db, pk)
        if not channel:
            raise errors.NotFoundError(msg='支付渠道不存在')
        return channel

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        select_stmt = await pay_channel_dao.get_select()
        return await paging_data(db, select_stmt)

    @staticmethod
    async def get_active_channels(*, db: AsyncSession) -> Sequence[PayChannel]:
        """获取启用的渠道列表（用户端展示用，不含密钥）"""
        return await pay_channel_dao.get_active(db)

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreatePayChannelParam) -> None:
        existing = await pay_channel_dao.get_by_code(db, obj.code)
        if existing:
            raise errors.ForbiddenError(msg=f'渠道 {obj.code} 已存在')
        await pay_channel_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdatePayChannelParam) -> int:
        return await pay_channel_dao.update(db, pk, obj)

    @staticmethod
    async def update_status(*, db: AsyncSession, pk: int, status: int) -> int:
        return await pay_channel_dao.update_status(db, pk, status)

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeletePayChannelParam) -> int:
        return await pay_channel_dao.delete(db, obj.pks)


pay_channel_service: PayChannelService = PayChannelService()

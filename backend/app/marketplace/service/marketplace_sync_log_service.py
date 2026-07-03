from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.marketplace.crud.crud_marketplace_sync_log import marketplace_sync_log_dao
from backend.app.marketplace.model import MarketplaceSyncLog
from backend.app.marketplace.schema.marketplace_sync_log import (
    CreateMarketplaceSyncLogParam,
    DeleteMarketplaceSyncLogParam,
    UpdateMarketplaceSyncLogParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class MarketplaceSyncLogService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> MarketplaceSyncLog:
        """
        获取技能市场同步日志

        :param db: 数据库会话
        :param pk: 技能市场同步日志 ID
        :return:
        """
        marketplace_sync_log = await marketplace_sync_log_dao.get(db, pk)
        if not marketplace_sync_log:
            raise errors.NotFoundError(msg='技能市场同步日志不存在')
        return marketplace_sync_log

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取技能市场同步日志列表

        :param db: 数据库会话
        :return:
        """
        marketplace_sync_log_select = await marketplace_sync_log_dao.get_select()
        return await paging_data(db, marketplace_sync_log_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[MarketplaceSyncLog]:
        """
        获取所有技能市场同步日志

        :param db: 数据库会话
        :return:
        """
        marketplace_sync_log_list = await marketplace_sync_log_dao.get_all(db)
        return marketplace_sync_log_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateMarketplaceSyncLogParam) -> None:
        """
        创建技能市场同步日志

        :param db: 数据库会话
        :param obj: 创建技能市场同步日志参数
        :return:
        """
        await marketplace_sync_log_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateMarketplaceSyncLogParam) -> int:
        """
        更新技能市场同步日志

        :param db: 数据库会话
        :param pk: 技能市场同步日志 ID
        :param obj: 更新技能市场同步日志参数
        :return:
        """
        count = await marketplace_sync_log_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteMarketplaceSyncLogParam) -> int:
        """
        删除技能市场同步日志

        :param db: 数据库会话
        :param obj: 技能市场同步日志 ID 列表
        :return:
        """
        count = await marketplace_sync_log_dao.delete(db, obj.pks)
        return count


marketplace_sync_log_service: MarketplaceSyncLogService = MarketplaceSyncLogService()

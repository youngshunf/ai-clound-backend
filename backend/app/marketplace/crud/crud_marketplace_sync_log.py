from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.marketplace.model import MarketplaceSyncLog
from backend.app.marketplace.schema.marketplace_sync_log import (
    CreateMarketplaceSyncLogParam,
    UpdateMarketplaceSyncLogParam,
)


class CRUDMarketplaceSyncLog(CRUDPlus[MarketplaceSyncLog]):
    async def get(self, db: AsyncSession, pk: int) -> MarketplaceSyncLog | None:
        """
        获取技能市场同步日志

        :param db: 数据库会话
        :param pk: 技能市场同步日志 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取技能市场同步日志列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[MarketplaceSyncLog]:
        """
        获取所有技能市场同步日志

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateMarketplaceSyncLogParam) -> MarketplaceSyncLog:
        """
        创建技能市场同步日志

        :param db: 数据库会话
        :param obj: 创建技能市场同步日志参数
        :return: 创建的同步日志对象
        """
        return await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateMarketplaceSyncLogParam) -> int:
        """
        更新技能市场同步日志

        :param db: 数据库会话
        :param pk: 技能市场同步日志 ID
        :param obj: 更新 技能市场同步日志参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除技能市场同步日志

        :param db: 数据库会话
        :param pks: 技能市场同步日志 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


marketplace_sync_log_dao: CRUDMarketplaceSyncLog = CRUDMarketplaceSyncLog(MarketplaceSyncLog)

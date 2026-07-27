from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnSyncBusinessReceipts
from backend.app.hasn.schema.hasn_sync_business_receipts import CreateHasnSyncBusinessReceiptsParam, UpdateHasnSyncBusinessReceiptsParam


class CRUDHasnSyncBusinessReceipts(CRUDPlus[HasnSyncBusinessReceipts]):
    async def get(self, db: AsyncSession, pk: int) -> HasnSyncBusinessReceipts | None:
        """
        获取sync inbox 业务应用的事务内幂等回执

        :param db: 数据库会话
        :param pk: sync inbox 业务应用的事务内幂等回执 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取sync inbox 业务应用的事务内幂等回执列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnSyncBusinessReceipts]:
        """
        获取所有sync inbox 业务应用的事务内幂等回执

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnSyncBusinessReceiptsParam) -> None:
        """
        创建sync inbox 业务应用的事务内幂等回执

        :param db: 数据库会话
        :param obj: 创建sync inbox 业务应用的事务内幂等回执参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnSyncBusinessReceiptsParam) -> int:
        """
        更新sync inbox 业务应用的事务内幂等回执

        :param db: 数据库会话
        :param pk: sync inbox 业务应用的事务内幂等回执 ID
        :param obj: 更新 sync inbox 业务应用的事务内幂等回执参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除sync inbox 业务应用的事务内幂等回执

        :param db: 数据库会话
        :param pks: sync inbox 业务应用的事务内幂等回执 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_sync_business_receipts_dao: CRUDHasnSyncBusinessReceipts = CRUDHasnSyncBusinessReceipts(HasnSyncBusinessReceipts)

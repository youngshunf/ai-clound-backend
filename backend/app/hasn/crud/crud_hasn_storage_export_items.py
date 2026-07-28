from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnStorageExportItems
from backend.app.hasn.schema.hasn_storage_export_items import CreateHasnStorageExportItemsParam, UpdateHasnStorageExportItemsParam


class CRUDHasnStorageExportItems(CRUDPlus[HasnStorageExportItems]):
    async def get(self, db: AsyncSession, pk: int) -> HasnStorageExportItems | None:
        """
        获取用户云存储导出逐资产不可变快照

        :param db: 数据库会话
        :param pk: 用户云存储导出逐资产不可变快照 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取用户云存储导出逐资产不可变快照列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnStorageExportItems]:
        """
        获取所有用户云存储导出逐资产不可变快照

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnStorageExportItemsParam) -> None:
        """
        创建用户云存储导出逐资产不可变快照

        :param db: 数据库会话
        :param obj: 创建用户云存储导出逐资产不可变快照参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnStorageExportItemsParam) -> int:
        """
        更新用户云存储导出逐资产不可变快照

        :param db: 数据库会话
        :param pk: 用户云存储导出逐资产不可变快照 ID
        :param obj: 更新 用户云存储导出逐资产不可变快照参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除用户云存储导出逐资产不可变快照

        :param db: 数据库会话
        :param pks: 用户云存储导出逐资产不可变快照 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_storage_export_items_dao: CRUDHasnStorageExportItems = CRUDHasnStorageExportItems(HasnStorageExportItems)

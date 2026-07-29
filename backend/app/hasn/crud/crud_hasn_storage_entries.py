from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnStorageEntries
from backend.app.hasn.schema.hasn_storage_entries import CreateHasnStorageEntriesParam, UpdateHasnStorageEntriesParam


class CRUDHasnStorageEntries(CRUDPlus[HasnStorageEntries]):
    async def get(self, db: AsyncSession, pk: int) -> HasnStorageEntries | None:
        """
        获取用户云存储逻辑目录项

        :param db: 数据库会话
        :param pk: 用户云存储逻辑目录项 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取用户云存储逻辑目录项列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnStorageEntries]:
        """
        获取所有用户云存储逻辑目录项

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnStorageEntriesParam) -> None:
        """
        创建用户云存储逻辑目录项

        :param db: 数据库会话
        :param obj: 创建用户云存储逻辑目录项参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnStorageEntriesParam) -> int:
        """
        更新用户云存储逻辑目录项

        :param db: 数据库会话
        :param pk: 用户云存储逻辑目录项 ID
        :param obj: 更新 用户云存储逻辑目录项参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除用户云存储逻辑目录项

        :param db: 数据库会话
        :param pks: 用户云存储逻辑目录项 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_storage_entries_dao: CRUDHasnStorageEntries = CRUDHasnStorageEntries(HasnStorageEntries)

from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnChannelBindings
from backend.app.hasn.schema.hasn_channel_bindings import CreateHasnChannelBindingsParam, UpdateHasnChannelBindingsParam


class CRUDHasnChannelBindings(CRUDPlus[HasnChannelBindings]):
    async def get(self, db: AsyncSession, pk: int) -> HasnChannelBindings | None:
        """
        获取HASN Channel Binding

        :param db: 数据库会话
        :param pk: HASN Channel Binding  ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN Channel Binding 列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnChannelBindings]:
        """
        获取所有HASN Channel Binding

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnChannelBindingsParam) -> None:
        """
        创建HASN Channel Binding

        :param db: 数据库会话
        :param obj: 创建HASN Channel Binding 参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnChannelBindingsParam) -> int:
        """
        更新HASN Channel Binding

        :param db: 数据库会话
        :param pk: HASN Channel Binding  ID
        :param obj: 更新 HASN Channel Binding 参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN Channel Binding

        :param db: 数据库会话
        :param pks: HASN Channel Binding  ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_channel_bindings_dao: CRUDHasnChannelBindings = CRUDHasnChannelBindings(HasnChannelBindings)

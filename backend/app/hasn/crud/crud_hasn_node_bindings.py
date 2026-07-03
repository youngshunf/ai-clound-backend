from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnNodeBindings
from backend.app.hasn.schema.hasn_node_bindings import CreateHasnNodeBindingsParam, UpdateHasnNodeBindingsParam


class CRUDHasnNodeBindings(CRUDPlus[HasnNodeBindings]):
    async def get(self, db: AsyncSession, pk: int) -> HasnNodeBindings | None:
        """
        获取HASN Node Owner Binding 租约

        :param db: 数据库会话
        :param pk: HASN Node Owner Binding 租约 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN Node Owner Binding 租约列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnNodeBindings]:
        """
        获取所有HASN Node Owner Binding 租约

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnNodeBindingsParam) -> None:
        """
        创建HASN Node Owner Binding 租约

        :param db: 数据库会话
        :param obj: 创建HASN Node Owner Binding 租约参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnNodeBindingsParam) -> int:
        """
        更新HASN Node Owner Binding 租约

        :param db: 数据库会话
        :param pk: HASN Node Owner Binding 租约 ID
        :param obj: 更新 HASN Node Owner Binding 租约参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN Node Owner Binding 租约

        :param db: 数据库会话
        :param pks: HASN Node Owner Binding 租约 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_node_bindings_dao: CRUDHasnNodeBindings = CRUDHasnNodeBindings(HasnNodeBindings)

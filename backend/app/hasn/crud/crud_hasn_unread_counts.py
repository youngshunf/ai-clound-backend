from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn.model import HasnUnreadCounts
from backend.app.hasn.schema.hasn_unread_counts import CreateHasnUnreadCountsParam, UpdateHasnUnreadCountsParam


class CRUDHasnUnreadCounts(CRUDPlus[HasnUnreadCounts]):
    async def get(self, db: AsyncSession, pk: int) -> HasnUnreadCounts | None:
        """
        获取HASN 未读计数

        :param db: 数据库会话
        :param pk: HASN 未读计数 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取HASN 未读计数列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnUnreadCounts]:
        """
        获取所有HASN 未读计数

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnUnreadCountsParam) -> None:
        """
        创建HASN 未读计数

        :param db: 数据库会话
        :param obj: 创建HASN 未读计数参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnUnreadCountsParam) -> int:
        """
        更新HASN 未读计数

        :param db: 数据库会话
        :param pk: HASN 未读计数 ID
        :param obj: 更新 HASN 未读计数参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除HASN 未读计数

        :param db: 数据库会话
        :param pks: HASN 未读计数 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_unread_counts_dao: CRUDHasnUnreadCounts = CRUDHasnUnreadCounts(HasnUnreadCounts)

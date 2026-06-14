from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_deck.model import Page
from backend.app.hasn_deck.schema.page import CreatePageParam, UpdatePageParam


class CRUDPage(CRUDPlus[Page]):
    async def get(self, db: AsyncSession, pk: int) -> Page | None:
        """
        获取演示文稿幻灯片（云端权威）

        :param db: 数据库会话
        :param pk: 演示文稿幻灯片（云端权威） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取演示文稿幻灯片（云端权威）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Page]:
        """
        获取所有演示文稿幻灯片（云端权威）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreatePageParam) -> None:
        """
        创建演示文稿幻灯片（云端权威）

        :param db: 数据库会话
        :param obj: 创建演示文稿幻灯片（云端权威）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdatePageParam) -> int:
        """
        更新演示文稿幻灯片（云端权威）

        :param db: 数据库会话
        :param pk: 演示文稿幻灯片（云端权威） ID
        :param obj: 更新 演示文稿幻灯片（云端权威）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除演示文稿幻灯片（云端权威）

        :param db: 数据库会话
        :param pks: 演示文稿幻灯片（云端权威） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


page_dao: CRUDPage = CRUDPage(Page)

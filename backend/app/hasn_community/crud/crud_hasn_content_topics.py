from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_community.model import HasnContentTopics
from backend.app.hasn_community.schema.hasn_content_topics import CreateHasnContentTopicsParam, UpdateHasnContentTopicsParam


class CRUDHasnContentTopics(CRUDPlus[HasnContentTopics]):
    async def get(self, db: AsyncSession, pk: int) -> HasnContentTopics | None:
        """
        获取内容与话题关联

        :param db: 数据库会话
        :param pk: 内容与话题关联 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取内容与话题关联列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnContentTopics]:
        """
        获取所有内容与话题关联

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnContentTopicsParam) -> None:
        """
        创建内容与话题关联

        :param db: 数据库会话
        :param obj: 创建内容与话题关联参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnContentTopicsParam) -> int:
        """
        更新内容与话题关联

        :param db: 数据库会话
        :param pk: 内容与话题关联 ID
        :param obj: 更新 内容与话题关联参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除内容与话题关联

        :param db: 数据库会话
        :param pks: 内容与话题关联 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_content_topics_dao: CRUDHasnContentTopics = CRUDHasnContentTopics(HasnContentTopics)

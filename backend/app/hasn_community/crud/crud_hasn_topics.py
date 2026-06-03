from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_community.model import HasnTopics
from backend.app.hasn_community.schema.hasn_topics import CreateHasnTopicsParam, UpdateHasnTopicsParam


class CRUDHasnTopics(CRUDPlus[HasnTopics]):
    async def get(self, db: AsyncSession, pk: int) -> HasnTopics | None:
        """
        获取社区话题实体

        :param db: 数据库会话
        :param pk: 社区话题实体 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取社区话题实体列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HasnTopics]:
        """
        获取所有社区话题实体

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHasnTopicsParam) -> None:
        """
        创建社区话题实体

        :param db: 数据库会话
        :param obj: 创建社区话题实体参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHasnTopicsParam) -> int:
        """
        更新社区话题实体

        :param db: 数据库会话
        :param pk: 社区话题实体 ID
        :param obj: 更新 社区话题实体参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除社区话题实体

        :param db: 数据库会话
        :param pks: 社区话题实体 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hasn_topics_dao: CRUDHasnTopics = CRUDHasnTopics(HasnTopics)

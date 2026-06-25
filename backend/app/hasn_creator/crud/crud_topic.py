from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_creator.model import Topic
from backend.app.hasn_creator.schema.topic import CreateTopicParam, UpdateTopicParam


class CRUDTopic(CRUDPlus[Topic]):
    async def get(self, db: AsyncSession, pk: int) -> Topic | None:
        """
        获取选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过

        :param db: 数据库会话
        :param pk: 选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[Topic]:
        """
        获取所有选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateTopicParam) -> None:
        """
        创建选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过

        :param db: 数据库会话
        :param obj: 创建选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateTopicParam) -> int:
        """
        更新选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过

        :param db: 数据库会话
        :param pk: 选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID
        :param obj: 更新 选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过

        :param db: 数据库会话
        :param pks: 选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


topic_dao: CRUDTopic = CRUDTopic(Topic)

from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_creator.crud.crud_topic import topic_dao
from backend.app.hasn_creator.model import Topic
from backend.app.hasn_creator.schema.topic import CreateTopicParam, DeleteTopicParam, UpdateTopicParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class TopicService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> Topic:
        """
        获取选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过

        :param db: 数据库会话
        :param pk: 选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID
        :return:
        """
        topic = await topic_dao.get(db, pk)
        if not topic:
            raise errors.NotFoundError(msg='选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过不存在')
        return topic

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过列表

        :param db: 数据库会话
        :return:
        """
        topic_select = await topic_dao.get_select()
        return await paging_data(db, topic_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[Topic]:
        """
        获取所有选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过

        :param db: 数据库会话
        :return:
        """
        topic_list = await topic_dao.get_all(db)
        return topic_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateTopicParam) -> None:
        """
        创建选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过

        :param db: 数据库会话
        :param obj: 创建选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过参数
        :return:
        """
        await topic_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateTopicParam) -> int:
        """
        更新选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过

        :param db: 数据库会话
        :param pk: 选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID
        :param obj: 更新选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过参数
        :return:
        """
        count = await topic_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteTopicParam) -> int:
        """
        删除选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过

        :param db: 数据库会话
        :param obj: 选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过 ID 列表
        :return:
        """
        count = await topic_dao.delete(db, obj.pks)
        return count


topic_service: TopicService = TopicService()

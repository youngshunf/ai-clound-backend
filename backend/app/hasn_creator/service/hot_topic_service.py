from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_creator.crud.crud_hot_topic import hot_topic_dao
from backend.app.hasn_creator.model import HotTopic
from backend.app.hasn_creator.schema.hot_topic import CreateHotTopicParam, DeleteHotTopicParam, UpdateHotTopicParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class HotTopicService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> HotTopic:
        """
        获取热榜快照（全局，去重，喂选题；可选数据源）

        :param db: 数据库会话
        :param pk: 热榜快照（全局，去重，喂选题；可选数据源） ID
        :return:
        """
        hot_topic = await hot_topic_dao.get(db, pk)
        if not hot_topic:
            raise errors.NotFoundError(msg='热榜快照（全局，去重，喂选题；可选数据源）不存在')
        return hot_topic

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取热榜快照（全局，去重，喂选题；可选数据源）列表

        :param db: 数据库会话
        :return:
        """
        hot_topic_select = await hot_topic_dao.get_select()
        return await paging_data(db, hot_topic_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[HotTopic]:
        """
        获取所有热榜快照（全局，去重，喂选题；可选数据源）

        :param db: 数据库会话
        :return:
        """
        hot_topic_list = await hot_topic_dao.get_all(db)
        return hot_topic_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateHotTopicParam) -> None:
        """
        创建热榜快照（全局，去重，喂选题；可选数据源）

        :param db: 数据库会话
        :param obj: 创建热榜快照（全局，去重，喂选题；可选数据源）参数
        :return:
        """
        await hot_topic_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateHotTopicParam) -> int:
        """
        更新热榜快照（全局，去重，喂选题；可选数据源）

        :param db: 数据库会话
        :param pk: 热榜快照（全局，去重，喂选题；可选数据源） ID
        :param obj: 更新热榜快照（全局，去重，喂选题；可选数据源）参数
        :return:
        """
        count = await hot_topic_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteHotTopicParam) -> int:
        """
        删除热榜快照（全局，去重，喂选题；可选数据源）

        :param db: 数据库会话
        :param obj: 热榜快照（全局，去重，喂选题；可选数据源） ID 列表
        :return:
        """
        count = await hot_topic_dao.delete(db, obj.pks)
        return count


hot_topic_service: HotTopicService = HotTopicService()

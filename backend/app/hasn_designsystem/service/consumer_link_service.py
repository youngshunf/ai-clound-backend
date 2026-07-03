from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_designsystem.crud.crud_consumer_link import consumer_link_dao
from backend.app.hasn_designsystem.model import ConsumerLink
from backend.app.hasn_designsystem.schema.consumer_link import (
    CreateConsumerLinkParam,
    DeleteConsumerLinkParam,
    UpdateConsumerLinkParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ConsumerLinkService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> ConsumerLink:
        """
        获取设计系统下游消费登记（换系统重渲染追踪）

        :param db: 数据库会话
        :param pk: 设计系统下游消费登记（换系统重渲染追踪） ID
        :return:
        """
        consumer_link = await consumer_link_dao.get(db, pk)
        if not consumer_link:
            raise errors.NotFoundError(msg='设计系统下游消费登记（换系统重渲染追踪）不存在')
        return consumer_link

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取设计系统下游消费登记（换系统重渲染追踪）列表

        :param db: 数据库会话
        :return:
        """
        consumer_link_select = await consumer_link_dao.get_select()
        return await paging_data(db, consumer_link_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[ConsumerLink]:
        """
        获取所有设计系统下游消费登记（换系统重渲染追踪）

        :param db: 数据库会话
        :return:
        """
        consumer_link_list = await consumer_link_dao.get_all(db)
        return consumer_link_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateConsumerLinkParam) -> None:
        """
        创建设计系统下游消费登记（换系统重渲染追踪）

        :param db: 数据库会话
        :param obj: 创建设计系统下游消费登记（换系统重渲染追踪）参数
        :return:
        """
        await consumer_link_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateConsumerLinkParam) -> int:
        """
        更新设计系统下游消费登记（换系统重渲染追踪）

        :param db: 数据库会话
        :param pk: 设计系统下游消费登记（换系统重渲染追踪） ID
        :param obj: 更新设计系统下游消费登记（换系统重渲染追踪）参数
        :return:
        """
        count = await consumer_link_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteConsumerLinkParam) -> int:
        """
        删除设计系统下游消费登记（换系统重渲染追踪）

        :param db: 数据库会话
        :param obj: 设计系统下游消费登记（换系统重渲染追踪） ID 列表
        :return:
        """
        count = await consumer_link_dao.delete(db, obj.pks)
        return count


consumer_link_service: ConsumerLinkService = ConsumerLinkService()

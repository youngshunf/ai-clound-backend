from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_designsystem.model import ConsumerLink
from backend.app.hasn_designsystem.schema.consumer_link import CreateConsumerLinkParam, UpdateConsumerLinkParam


class CRUDConsumerLink(CRUDPlus[ConsumerLink]):
    async def get(self, db: AsyncSession, pk: int) -> ConsumerLink | None:
        """
        获取设计系统下游消费登记（换系统重渲染追踪）

        :param db: 数据库会话
        :param pk: 设计系统下游消费登记（换系统重渲染追踪） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取设计系统下游消费登记（换系统重渲染追踪）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[ConsumerLink]:
        """
        获取所有设计系统下游消费登记（换系统重渲染追踪）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateConsumerLinkParam) -> None:
        """
        创建设计系统下游消费登记（换系统重渲染追踪）

        :param db: 数据库会话
        :param obj: 创建设计系统下游消费登记（换系统重渲染追踪）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateConsumerLinkParam) -> int:
        """
        更新设计系统下游消费登记（换系统重渲染追踪）

        :param db: 数据库会话
        :param pk: 设计系统下游消费登记（换系统重渲染追踪） ID
        :param obj: 更新 设计系统下游消费登记（换系统重渲染追踪）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除设计系统下游消费登记（换系统重渲染追踪）

        :param db: 数据库会话
        :param pks: 设计系统下游消费登记（换系统重渲染追踪） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


consumer_link_dao: CRUDConsumerLink = CRUDConsumerLink(ConsumerLink)

from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_creator.model import HotTopic
from backend.app.hasn_creator.schema.hot_topic import CreateHotTopicParam, UpdateHotTopicParam


class CRUDHotTopic(CRUDPlus[HotTopic]):
    async def get(self, db: AsyncSession, pk: int) -> HotTopic | None:
        """
        获取热榜快照（全局，去重，喂选题；可选数据源）

        :param db: 数据库会话
        :param pk: 热榜快照（全局，去重，喂选题；可选数据源） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取热榜快照（全局，去重，喂选题；可选数据源）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[HotTopic]:
        """
        获取所有热榜快照（全局，去重，喂选题；可选数据源）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateHotTopicParam) -> None:
        """
        创建热榜快照（全局，去重，喂选题；可选数据源）

        :param db: 数据库会话
        :param obj: 创建热榜快照（全局，去重，喂选题；可选数据源）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateHotTopicParam) -> int:
        """
        更新热榜快照（全局，去重，喂选题；可选数据源）

        :param db: 数据库会话
        :param pk: 热榜快照（全局，去重，喂选题；可选数据源） ID
        :param obj: 更新 热榜快照（全局，去重，喂选题；可选数据源）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除热榜快照（全局，去重，喂选题；可选数据源）

        :param db: 数据库会话
        :param pks: 热榜快照（全局，去重，喂选题；可选数据源） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


hot_topic_dao: CRUDHotTopic = CRUDHotTopic(HotTopic)

from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_creator.model import ContentInsight
from backend.app.hasn_creator.schema.content_insight import CreateContentInsightParam, UpdateContentInsightParam


class CRUDContentInsight(CRUDPlus[ContentInsight]):
    async def get(self, db: AsyncSession, pk: int) -> ContentInsight | None:
        """
        获取内容洞察（复盘结构化结论，进化沉淀核心）

        :param db: 数据库会话
        :param pk: 内容洞察（复盘结构化结论，进化沉淀核心） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取内容洞察（复盘结构化结论，进化沉淀核心）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[ContentInsight]:
        """
        获取所有内容洞察（复盘结构化结论，进化沉淀核心）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateContentInsightParam) -> None:
        """
        创建内容洞察（复盘结构化结论，进化沉淀核心）

        :param db: 数据库会话
        :param obj: 创建内容洞察（复盘结构化结论，进化沉淀核心）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateContentInsightParam) -> int:
        """
        更新内容洞察（复盘结构化结论，进化沉淀核心）

        :param db: 数据库会话
        :param pk: 内容洞察（复盘结构化结论，进化沉淀核心） ID
        :param obj: 更新 内容洞察（复盘结构化结论，进化沉淀核心）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除内容洞察（复盘结构化结论，进化沉淀核心）

        :param db: 数据库会话
        :param pks: 内容洞察（复盘结构化结论，进化沉淀核心） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


content_insight_dao: CRUDContentInsight = CRUDContentInsight(ContentInsight)

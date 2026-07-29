from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import GrowthProfileSuggestion
from backend.app.hasn_growth.schema.growth_profile_suggestion import (
    CreateGrowthProfileSuggestionParam,
    UpdateGrowthProfileSuggestionParam,
)


class CRUDGrowthProfileSuggestion(CRUDPlus[GrowthProfileSuggestion]):
    async def get(self, db: AsyncSession, pk: int) -> GrowthProfileSuggestion | None:
        """
        获取分身或系统提出、等待主人确认的画像建议

        :param db: 数据库会话
        :param pk: 分身或系统提出、等待主人确认的画像建议 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取分身或系统提出、等待主人确认的画像建议列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[GrowthProfileSuggestion]:
        """
        获取所有分身或系统提出、等待主人确认的画像建议

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateGrowthProfileSuggestionParam) -> None:
        """
        创建分身或系统提出、等待主人确认的画像建议

        :param db: 数据库会话
        :param obj: 创建分身或系统提出、等待主人确认的画像建议参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateGrowthProfileSuggestionParam) -> int:
        """
        更新分身或系统提出、等待主人确认的画像建议

        :param db: 数据库会话
        :param pk: 分身或系统提出、等待主人确认的画像建议 ID
        :param obj: 更新 分身或系统提出、等待主人确认的画像建议参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除分身或系统提出、等待主人确认的画像建议

        :param db: 数据库会话
        :param pks: 分身或系统提出、等待主人确认的画像建议 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


growth_profile_suggestion_dao: CRUDGrowthProfileSuggestion = CRUDGrowthProfileSuggestion(GrowthProfileSuggestion)

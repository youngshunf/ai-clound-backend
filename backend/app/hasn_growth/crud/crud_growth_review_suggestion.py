from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import GrowthReviewSuggestion
from backend.app.hasn_growth.schema.growth_review_suggestion import (
    CreateGrowthReviewSuggestionParam,
    UpdateGrowthReviewSuggestionParam,
)


class CRUDGrowthReviewSuggestion(CRUDPlus[GrowthReviewSuggestion]):
    async def get(self, db: AsyncSession, pk: int) -> GrowthReviewSuggestion | None:
        """
        获取下一周期 ICP、渠道与打法建议及 Owner 审阅结果

        :param db: 数据库会话
        :param pk: 下一周期 ICP、渠道与打法建议及 Owner 审阅结果 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取下一周期 ICP、渠道与打法建议及 Owner 审阅结果列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[GrowthReviewSuggestion]:
        """
        获取所有下一周期 ICP、渠道与打法建议及 Owner 审阅结果

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateGrowthReviewSuggestionParam) -> None:
        """
        创建下一周期 ICP、渠道与打法建议及 Owner 审阅结果

        :param db: 数据库会话
        :param obj: 创建下一周期 ICP、渠道与打法建议及 Owner 审阅结果参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateGrowthReviewSuggestionParam) -> int:
        """
        更新下一周期 ICP、渠道与打法建议及 Owner 审阅结果

        :param db: 数据库会话
        :param pk: 下一周期 ICP、渠道与打法建议及 Owner 审阅结果 ID
        :param obj: 更新 下一周期 ICP、渠道与打法建议及 Owner 审阅结果参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除下一周期 ICP、渠道与打法建议及 Owner 审阅结果

        :param db: 数据库会话
        :param pks: 下一周期 ICP、渠道与打法建议及 Owner 审阅结果 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


growth_review_suggestion_dao: CRUDGrowthReviewSuggestion = CRUDGrowthReviewSuggestion(GrowthReviewSuggestion)

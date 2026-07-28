from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_growth.model import GrowthAttributionEvent
from backend.app.hasn_growth.schema.growth_attribution_event import (
    CreateGrowthAttributionEventParam,
    UpdateGrowthAttributionEventParam,
)


class CRUDGrowthAttributionEvent(CRUDPlus[GrowthAttributionEvent]):
    async def get(self, db: AsyncSession, pk: int) -> GrowthAttributionEvent | None:
        """
        获取可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实

        :param db: 数据库会话
        :param pk: 可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[GrowthAttributionEvent]:
        """
        获取所有可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateGrowthAttributionEventParam) -> None:
        """
        创建可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实

        :param db: 数据库会话
        :param obj: 创建可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateGrowthAttributionEventParam) -> int:
        """
        更新可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实

        :param db: 数据库会话
        :param pk: 可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实 ID
        :param obj: 更新 可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实

        :param db: 数据库会话
        :param pks: 可按漏斗重算转化、收入、成本和赢输分布的追加式归因事实 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


growth_attribution_event_dao: CRUDGrowthAttributionEvent = CRUDGrowthAttributionEvent(GrowthAttributionEvent)

from collections.abc import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.hasn_quant.model import QuantStrategy
from backend.app.hasn_quant.schema.quant_strategy import CreateQuantStrategyParam, UpdateQuantStrategyParam


class CRUDQuantStrategy(CRUDPlus[QuantStrategy]):
    async def get(self, db: AsyncSession, pk: int) -> QuantStrategy | None:
        """
        获取量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）

        :param db: 数据库会话
        :param pk: 量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[QuantStrategy]:
        """
        获取所有量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateQuantStrategyParam) -> None:
        """
        创建量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）

        :param db: 数据库会话
        :param obj: 创建量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateQuantStrategyParam) -> int:
        """
        更新量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）

        :param db: 数据库会话
        :param pk: 量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID
        :param obj: 更新 量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）

        :param db: 数据库会话
        :param pks: 量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


quant_strategy_dao: CRUDQuantStrategy = CRUDQuantStrategy(QuantStrategy)

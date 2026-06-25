from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn_quant.crud.crud_quant_strategy import quant_strategy_dao
from backend.app.hasn_quant.model import QuantStrategy
from backend.app.hasn_quant.schema.quant_strategy import (
    CreateQuantStrategyParam,
    DeleteQuantStrategyParam,
    UpdateQuantStrategyParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class QuantStrategyService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> QuantStrategy:
        """
        获取量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）

        :param db: 数据库会话
        :param pk: 量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID
        :return:
        """
        quant_strategy = await quant_strategy_dao.get(db, pk)
        if not quant_strategy:
            raise errors.NotFoundError(msg='量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）不存在')
        return quant_strategy

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）列表

        :param db: 数据库会话
        :return:
        """
        quant_strategy_select = await quant_strategy_dao.get_select()
        return await paging_data(db, quant_strategy_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[QuantStrategy]:
        """
        获取所有量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）

        :param db: 数据库会话
        :return:
        """
        quant_strategy_list = await quant_strategy_dao.get_all(db)
        return quant_strategy_list

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateQuantStrategyParam) -> None:
        """
        创建量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）

        :param db: 数据库会话
        :param obj: 创建量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）参数
        :return:
        """
        await quant_strategy_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateQuantStrategyParam) -> int:
        """
        更新量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）

        :param db: 数据库会话
        :param pk: 量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID
        :param obj: 更新量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）参数
        :return:
        """
        count = await quant_strategy_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteQuantStrategyParam) -> int:
        """
        删除量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）

        :param db: 数据库会话
        :param obj: 量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID 列表
        :return:
        """
        count = await quant_strategy_dao.delete(db, obj.pks)
        return count


quant_strategy_service: QuantStrategyService = QuantStrategyService()

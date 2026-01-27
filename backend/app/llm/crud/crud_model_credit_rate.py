from typing import Sequence

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.llm.model import ModelCreditRate
from backend.app.llm.schema.model_credit_rate import CreateModelCreditRateParam, UpdateModelCreditRateParam


class CRUDModelCreditRate(CRUDPlus[ModelCreditRate]):
    async def get(self, db: AsyncSession, pk: int) -> ModelCreditRate | None:
        """
        获取模型积分费率表 - 定义不同模型的积分消耗规则

        :param db: 数据库会话
        :param pk: 模型积分费率表 - 定义不同模型的积分消耗规则 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_select(self) -> Select:
        """获取模型积分费率表 - 定义不同模型的积分消耗规则列表查询表达式"""
        return await self.select_order('id', 'desc')

    async def get_all(self, db: AsyncSession) -> Sequence[ModelCreditRate]:
        """
        获取所有模型积分费率表 - 定义不同模型的积分消耗规则

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateModelCreditRateParam) -> None:
        """
        创建模型积分费率表 - 定义不同模型的积分消耗规则

        :param db: 数据库会话
        :param obj: 创建模型积分费率表 - 定义不同模型的积分消耗规则参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateModelCreditRateParam) -> int:
        """
        更新模型积分费率表 - 定义不同模型的积分消耗规则

        :param db: 数据库会话
        :param pk: 模型积分费率表 - 定义不同模型的积分消耗规则 ID
        :param obj: 更新 模型积分费率表 - 定义不同模型的积分消耗规则参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除模型积分费率表 - 定义不同模型的积分消耗规则

        :param db: 数据库会话
        :param pks: 模型积分费率表 - 定义不同模型的积分消耗规则 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


model_credit_rate_dao: CRUDModelCreditRate = CRUDModelCreditRate(ModelCreditRate)

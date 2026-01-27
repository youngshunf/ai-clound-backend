from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.crud.crud_model_credit_rate import model_credit_rate_dao
from backend.app.llm.model import ModelCreditRate
from backend.app.llm.schema.model_credit_rate import CreateModelCreditRateParam, DeleteModelCreditRateParam, UpdateModelCreditRateParam
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ModelCreditRateService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> ModelCreditRate:
        """
        获取模型积分费率表 - 定义不同模型的积分消耗规则

        :param db: 数据库会话
        :param pk: 模型积分费率表 - 定义不同模型的积分消耗规则 ID
        :return:
        """
        model_credit_rate = await model_credit_rate_dao.get(db, pk)
        if not model_credit_rate:
            raise errors.NotFoundError(msg='模型积分费率表 - 定义不同模型的积分消耗规则不存在')
        return model_credit_rate

    @staticmethod
    async def get_list(db: AsyncSession) -> dict[str, Any]:
        """
        获取模型积分费率表 - 定义不同模型的积分消耗规则列表

        :param db: 数据库会话
        :return:
        """
        model_credit_rate_select = await model_credit_rate_dao.get_select()
        return await paging_data(db, model_credit_rate_select)

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[ModelCreditRate]:
        """
        获取所有模型积分费率表 - 定义不同模型的积分消耗规则

        :param db: 数据库会话
        :return:
        """
        model_credit_rates = await model_credit_rate_dao.get_all(db)
        return model_credit_rates

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateModelCreditRateParam) -> None:
        """
        创建模型积分费率表 - 定义不同模型的积分消耗规则

        :param db: 数据库会话
        :param obj: 创建模型积分费率表 - 定义不同模型的积分消耗规则参数
        :return:
        """
        await model_credit_rate_dao.create(db, obj)

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateModelCreditRateParam) -> int:
        """
        更新模型积分费率表 - 定义不同模型的积分消耗规则

        :param db: 数据库会话
        :param pk: 模型积分费率表 - 定义不同模型的积分消耗规则 ID
        :param obj: 更新模型积分费率表 - 定义不同模型的积分消耗规则参数
        :return:
        """
        count = await model_credit_rate_dao.update(db, pk, obj)
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteModelCreditRateParam) -> int:
        """
        删除模型积分费率表 - 定义不同模型的积分消耗规则

        :param db: 数据库会话
        :param obj: 模型积分费率表 - 定义不同模型的积分消耗规则 ID 列表
        :return:
        """
        count = await model_credit_rate_dao.delete(db, obj.pks)
        return count


model_credit_rate_service: ModelCreditRateService = ModelCreditRateService()

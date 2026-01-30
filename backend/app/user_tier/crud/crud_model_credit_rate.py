from typing import Sequence

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.llm.model import ModelConfig, ModelProvider
from backend.app.user_tier.model import ModelCreditRate
from backend.app.user_tier.schema.model_credit_rate import CreateModelCreditRateParam, UpdateModelCreditRateParam


class CRUDModelCreditRate(CRUDPlus[ModelCreditRate]):
    async def get(self, db: AsyncSession, pk: int) -> ModelCreditRate | None:
        """
        获取模型积分费率

        :param db: 数据库会话
        :param pk: 模型积分费率 ID
        :return:
        """
        return await self.select_model(db, pk)

    async def get_list_with_model_info(
        self, db: AsyncSession, *, model_id: int | None = None
    ) -> list[dict]:
        """获取模型积分费率列表（包含模型信息）"""
        stmt = (
            select(
                ModelCreditRate,
                ModelConfig.model_name,
                ModelProvider.name.label('provider_name'),
            )
            .outerjoin(ModelConfig, ModelCreditRate.model_id == ModelConfig.id)
            .outerjoin(ModelProvider, ModelConfig.provider_id == ModelProvider.id)
            .order_by(ModelCreditRate.id.desc())
        )
        if model_id is not None:
            stmt = stmt.where(ModelCreditRate.model_id == model_id)
        result = await db.execute(stmt)
        rows = result.all()
        return rows

    async def get_all(self, db: AsyncSession) -> Sequence[ModelCreditRate]:
        """
        获取所有模型积分费率

        :param db: 数据库会话
        :return:
        """
        return await self.select_models(db)

    async def create(self, db: AsyncSession, obj: CreateModelCreditRateParam) -> None:
        """
        创建模型积分费率

        :param db: 数据库会话
        :param obj: 创建模型积分费率参数
        :return:
        """
        await self.create_model(db, obj)

    async def update(self, db: AsyncSession, pk: int, obj: UpdateModelCreditRateParam) -> int:
        """
        更新模型积分费率

        :param db: 数据库会话
        :param pk: 模型积分费率 ID
        :param obj: 更新 模型积分费率参数
        :return:
        """
        return await self.update_model(db, pk, obj)

    async def delete(self, db: AsyncSession, pks: list[int]) -> int:
        """
        批量删除模型积分费率

        :param db: 数据库会话
        :param pks: 模型积分费率 ID 列表
        :return:
        """
        return await self.delete_model_by_column(db, allow_multiple=True, id__in=pks)


model_credit_rate_dao: CRUDModelCreditRate = CRUDModelCreditRate(ModelCreditRate)

from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.user_tier.crud.crud_model_credit_rate import model_credit_rate_dao
from backend.app.user_tier.model import ModelCreditRate
from backend.app.user_tier.schema.model_credit_rate import (
    CreateModelCreditRateParam,
    DeleteModelCreditRateParam,
    UpdateModelCreditRateParam,
)
from backend.app.user_tier.service.credit_service import credit_service
from backend.common.exception import errors
from backend.common.log import log


class ModelCreditRateService:
    @staticmethod
    async def get(*, db: AsyncSession, pk: int) -> ModelCreditRate:
        """
        获取模型积分费率

        :param db: 数据库会话
        :param pk: 模型积分费率 ID
        :return:
        """
        model_credit_rate = await model_credit_rate_dao.get(db, pk)
        if not model_credit_rate:
            raise errors.NotFoundError(msg='模型积分费率不存在')
        return model_credit_rate

    @staticmethod
    async def get_list(db: AsyncSession, *, model_id: int | None = None) -> dict[str, Any]:
        """
        获取模型积分费率列表（包含模型名称和供应商）

        :param db: 数据库会话
        :param model_id: 模型 ID 筛选
        :return:
        """
        from math import ceil

        from fastapi_pagination import pagination_ctx
        from fastapi_pagination.ext.sqlalchemy import paginate
        from sqlalchemy import select, func

        from backend.app.llm.model import ModelConfig, ModelProvider

        # 构建查询
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

        # 执行分页查询
        page_result = await paginate(db, stmt)

        # 转换结果
        items = []
        for row in page_result.items:
            rate = row[0]  # ModelCreditRate 对象
            model_name = row[1]  # model_name
            provider_name = row[2]  # provider_name
            item_dict = {
                'id': rate.id,
                'model_id': rate.model_id,
                'model_name': model_name,
                'provider_name': provider_name,
                'base_credit_per_1k_tokens': float(rate.base_credit_per_1k_tokens) if rate.base_credit_per_1k_tokens else 0,
                'input_multiplier': float(rate.input_multiplier) if rate.input_multiplier else 0,
                'output_multiplier': float(rate.output_multiplier) if rate.output_multiplier else 0,
                'enabled': rate.enabled,
                'created_time': rate.created_time,
                'updated_time': rate.updated_time,
            }
            items.append(item_dict)

        # 构建分页响应
        page = page_result.page
        size = page_result.size
        total = page_result.total
        total_pages = ceil(total / size) if size > 0 else 0

        return {
            'items': items,
            'total': total,
            'page': page,
            'size': size,
            'total_pages': total_pages,
            'links': {
                'first': f'?page=1&size={size}',
                'last': f'?page={total_pages}&size={size}' if total_pages > 0 else f'?page=1&size={size}',
                'self': f'?page={page}&size={size}',
                'next': f'?page={page + 1}&size={size}' if page < total_pages else None,
                'prev': f'?page={page - 1}&size={size}' if page > 1 else None,
            },
        }

    @staticmethod
    async def get_all(*, db: AsyncSession) -> Sequence[ModelCreditRate]:
        """
        获取所有模型积分费率

        :param db: 数据库会话
        :return:
        """
        model_credit_rates = await model_credit_rate_dao.get_all(db)
        return model_credit_rates

    @staticmethod
    async def create(*, db: AsyncSession, obj: CreateModelCreditRateParam) -> None:
        """
        创建模型积分费率

        :param db: 数据库会话
        :param obj: 创建模型积分费率参数
        :return:
        """
        await model_credit_rate_dao.create(db, obj)
        # 失效该模型的费率缓存
        credit_service.invalidate_rate_cache(obj.model_id)
        log.info(f'[ModelCreditRate] Created rate for model_id={obj.model_id}, cache invalidated')

    @staticmethod
    async def update(*, db: AsyncSession, pk: int, obj: UpdateModelCreditRateParam) -> int:
        """
        更新模型积分费率

        :param db: 数据库会话
        :param pk: 模型积分费率 ID
        :param obj: 更新模型积分费率参数
        :return:
        """
        # 先获取原记录以获取 model_id
        rate = await model_credit_rate_dao.get(db, pk)
        count = await model_credit_rate_dao.update(db, pk, obj)
        if count > 0 and rate:
            # 失效该模型的费率缓存
            credit_service.invalidate_rate_cache(rate.model_id)
            log.info(f'[ModelCreditRate] Updated rate id={pk}, model_id={rate.model_id}, cache invalidated')
        return count

    @staticmethod
    async def delete(*, db: AsyncSession, obj: DeleteModelCreditRateParam) -> int:
        """
        删除模型积分费率

        :param db: 数据库会话
        :param obj: 模型积分费率 ID 列表
        :return:
        """
        # 先获取要删除的记录以获取 model_ids
        model_ids = []
        for pk in obj.pks:
            rate = await model_credit_rate_dao.get(db, pk)
            if rate:
                model_ids.append(rate.model_id)

        count = await model_credit_rate_dao.delete(db, obj.pks)
        if count > 0:
            # 失效相关模型的费率缓存
            for model_id in model_ids:
                credit_service.invalidate_rate_cache(model_id)
            log.info(f'[ModelCreditRate] Deleted {count} rates, model_ids={model_ids}, cache invalidated')
        return count


model_credit_rate_service: ModelCreditRateService = ModelCreditRateService()

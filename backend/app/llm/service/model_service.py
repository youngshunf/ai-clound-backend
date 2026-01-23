"""模型配置 Service"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.crud.crud_model_config import model_config_dao
from backend.app.llm.crud.crud_provider import provider_dao
from backend.app.llm.model.model_config import ModelConfig
from backend.app.llm.schema.model_config import (
    CreateModelConfigParam,
    GetAvailableModel,
    GetModelConfigDetail,
    UpdateModelConfigParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ModelService:
    """模型配置服务"""

    @staticmethod
    async def get(db: AsyncSession, pk: int) -> ModelConfig:
        """获取模型配置"""
        model = await model_config_dao.get(db, pk)
        if not model:
            raise errors.NotFoundError(msg='模型不存在')
        return model

    @staticmethod
    async def get_detail(db: AsyncSession, pk: int) -> GetModelConfigDetail:
        """获取模型配置详情"""
        model = await model_config_dao.get(db, pk)
        if not model:
            raise errors.NotFoundError(msg='模型不存在')

        provider = await provider_dao.get(db, model.provider_id)
        provider_name = provider.name if provider else None

        return GetModelConfigDetail(
            id=model.id,
            provider_id=model.provider_id,
            provider_name=provider_name,
            model_name=model.model_name,
            display_name=model.display_name,
            model_type=model.model_type,
            max_tokens=model.max_tokens,
            max_context_length=model.max_context_length,
            supports_streaming=model.supports_streaming,
            supports_tools=model.supports_tools,
            supports_vision=model.supports_vision,
            input_cost_per_1k=model.input_cost_per_1k,
            output_cost_per_1k=model.output_cost_per_1k,
            rpm_limit=model.rpm_limit,
            tpm_limit=model.tpm_limit,
            priority=model.priority,
            enabled=model.enabled,
        )

    @staticmethod
    async def get_list(
        db: AsyncSession,
        *,
        provider_id: int | None = None,
        model_type: str | None = None,
        model_name: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """获取模型配置列表（分页）"""
        stmt = await model_config_dao.get_list_with_provider(
            provider_id=provider_id,
            model_type=model_type,
            model_name=model_name,
            enabled=enabled,
        )
        page_data = await paging_data(db, stmt)
        return page_data

    @staticmethod
    async def get_available_models(db: AsyncSession) -> list[GetAvailableModel]:
        """
        获取可用模型列表（公开接口）

        返回格式与 agent-core ModelInfo 对应
        """
        models = await model_config_dao.get_all_enabled(db)
        return [
            GetAvailableModel(
                model_id=m.model_name,
                provider=m.provider.provider_type if m.provider else 'openai',
                display_name=m.display_name or m.model_name,
                max_tokens=m.max_tokens,
                model_type=m.model_type,
                supports_streaming=m.supports_streaming,
                supports_vision=m.supports_vision,
                supports_tools=m.supports_tools,
                priority=m.priority,
                enabled=m.enabled,
            )
            for m in models
        ]

    @staticmethod
    async def create(db: AsyncSession, obj: CreateModelConfigParam) -> None:
        """创建模型配置"""
        # 检查供应商是否存在
        provider = await provider_dao.get(db, obj.provider_id)
        if not provider:
            raise errors.NotFoundError(msg='供应商不存在')

        # 检查模型名称是否已存在
        existing = await model_config_dao.get_by_name(db, obj.model_name)
        if existing:
            raise errors.ForbiddenError(msg='模型名称已存在')

        await model_config_dao.create(db, obj)

    @staticmethod
    async def update(db: AsyncSession, pk: int, obj: UpdateModelConfigParam) -> int:
        """更新模型配置"""
        model = await model_config_dao.get(db, pk)
        if not model:
            raise errors.NotFoundError(msg='模型不存在')

        # 检查供应商是否存在
        if obj.provider_id:
            provider = await provider_dao.get(db, obj.provider_id)
            if not provider:
                raise errors.NotFoundError(msg='供应商不存在')

        # 检查模型名称是否重复
        if obj.model_name and obj.model_name != model.model_name:
            existing = await model_config_dao.get_by_name(db, obj.model_name)
            if existing:
                raise errors.ForbiddenError(msg='模型名称已存在')

        return await model_config_dao.update(db, pk, obj)

    @staticmethod
    async def delete(db: AsyncSession, pk: int) -> int:
        """删除模型配置"""
        model = await model_config_dao.get(db, pk)
        if not model:
            raise errors.NotFoundError(msg='模型不存在')
        return await model_config_dao.delete(db, pk)


model_service = ModelService()

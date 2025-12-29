"""模型配置 CRUD"""

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.llm.model.model_config import ModelConfig
from backend.app.llm.model.provider import ModelProvider
from backend.app.llm.schema.model_config import CreateModelConfigParam, UpdateModelConfigParam


class CRUDModelConfig(CRUDPlus[ModelConfig]):
    """模型配置数据库操作类"""

    async def get(self, db: AsyncSession, pk: int) -> ModelConfig | None:
        return await self.select_model(db, pk)

    async def get_by_name(self, db: AsyncSession, model_name: str) -> ModelConfig | None:
        return await self.select_model_by_column(db, model_name=model_name)

    async def get_list(
        self,
        *,
        provider_id: int | None = None,
        model_type: str | None = None,
        model_name: str | None = None,
        enabled: bool | None = None,
    ) -> Select:
        filters = {}
        if provider_id is not None:
            filters['provider_id'] = provider_id
        if model_type is not None:
            filters['model_type'] = model_type
        if model_name is not None:
            filters['model_name__like'] = f'%{model_name}%'
        if enabled is not None:
            filters['enabled'] = enabled
        return await self.select_order('priority', 'desc', **filters)

    async def get_list_with_provider(
        self,
        *,
        provider_id: int | None = None,
        model_type: str | None = None,
        model_name: str | None = None,
        enabled: bool | None = None,
    ) -> Select:
        """获取模型列表（带供应商名称）"""
        stmt = (
            select(
                ModelConfig.id,
                ModelConfig.provider_id,
                ModelProvider.name.label('provider_name'),
                ModelConfig.model_name,
                ModelConfig.display_name,
                ModelConfig.model_type,
                ModelConfig.max_tokens,
                ModelConfig.max_context_length,
                ModelConfig.supports_streaming,
                ModelConfig.supports_tools,
                ModelConfig.supports_vision,
                ModelConfig.priority,
                ModelConfig.enabled,
                ModelConfig.input_cost_per_1k,
                ModelConfig.output_cost_per_1k,
            )
            .outerjoin(ModelProvider, ModelConfig.provider_id == ModelProvider.id)
            .order_by(ModelConfig.created_time.desc())
        )
        if provider_id is not None:
            stmt = stmt.where(ModelConfig.provider_id == provider_id)
        if model_type is not None:
            stmt = stmt.where(ModelConfig.model_type == model_type)
        if model_name is not None:
            stmt = stmt.where(ModelConfig.model_name.like(f'%{model_name}%'))
        if enabled is not None:
            stmt = stmt.where(ModelConfig.enabled == enabled)
        return stmt

    async def get_all_enabled(self, db: AsyncSession) -> list[ModelConfig]:
        stmt = (
            select(ModelConfig)
            .options(selectinload(ModelConfig.provider))
            .where(ModelConfig.enabled == True)
            .order_by(ModelConfig.priority.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_provider(self, db: AsyncSession, provider_id: int) -> list[ModelConfig]:
        stmt = await self.select_order('priority', 'desc', provider_id=provider_id, enabled=True)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, obj: CreateModelConfigParam) -> None:
        await self.create_model(db, obj)
        await db.commit()

    async def update(self, db: AsyncSession, pk: int, obj: UpdateModelConfigParam) -> int:
        count = await self.update_model(db, pk, obj)
        await db.commit()
        return count

    async def delete(self, db: AsyncSession, pk: int) -> int:
        count = await self.delete_model(db, pk)
        await db.commit()
        return count


model_config_dao: CRUDModelConfig = CRUDModelConfig(ModelConfig)

"""模型供应商 CRUD"""

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.llm.model.provider import ModelProvider
from backend.app.llm.schema.provider import CreateProviderParam, UpdateProviderParam


class CRUDProvider(CRUDPlus[ModelProvider]):
    """模型供应商数据库操作类"""

    async def get(self, db: AsyncSession, pk: int) -> ModelProvider | None:
        return await self.select_model(db, pk)

    async def get_by_name(self, db: AsyncSession, name: str) -> ModelProvider | None:
        return await self.select_model_by_column(db, name=name)

    async def get_list(
        self,
        *,
        name: str | None = None,
        enabled: bool | None = None,
    ) -> Select:
        filters = {}
        if name is not None:
            filters['name__like'] = f'%{name}%'
        if enabled is not None:
            filters['enabled'] = enabled
        return await self.select_order('id', 'desc', **filters)

    async def get_all_enabled(self, db: AsyncSession) -> list[ModelProvider]:
        stmt = await self.select_order('id', 'asc', enabled=True)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, obj: CreateProviderParam, api_key_encrypted: str | None = None) -> None:
        create_data = obj.model_dump(exclude={'api_key'})
        create_data['api_key_encrypted'] = api_key_encrypted
        provider = ModelProvider(**create_data)
        db.add(provider)
        await db.commit()

    async def update(self, db: AsyncSession, pk: int, obj: UpdateProviderParam, api_key_encrypted: str | None = None) -> int:
        update_data = obj.model_dump(exclude={'api_key'}, exclude_none=True)
        if api_key_encrypted is not None:
            update_data['api_key_encrypted'] = api_key_encrypted
        count = await self.update_model(db, pk, update_data)
        await db.commit()
        return count

    async def delete(self, db: AsyncSession, pk: int) -> int:
        count = await self.delete_model(db, pk)
        await db.commit()
        return count


provider_dao: CRUDProvider = CRUDProvider(ModelProvider)

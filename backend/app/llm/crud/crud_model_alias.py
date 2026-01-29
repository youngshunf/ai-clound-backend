"""模型别名映射 CRUD

@author Ysf
"""

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy_crud_plus import CRUDPlus

from backend.app.llm.model.model_alias import ModelAlias
from backend.app.llm.model.model_config import ModelConfig

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.llm.schema.model_alias import CreateModelAliasParam, UpdateModelAliasParam


class CRUDModelAlias(CRUDPlus[ModelAlias]):
    """模型别名映射数据库操作类"""

    async def get(self, db: AsyncSession, pk: int) -> ModelAlias | None:
        return await self.select_model(db, pk)

    async def get_by_alias(self, db: AsyncSession, alias_name: str) -> ModelAlias | None:
        """根据别名获取映射配置"""
        return await self.select_model_by_column(db, alias_name=alias_name, enabled=True)

    async def get_mapped_model_ids(self, db: AsyncSession, alias_name: str) -> list[int]:
        """根据别名获取映射的模型 ID 列表（按优先级排序）

        Args:
            db: 数据库会话
            alias_name: 别名（如 claude-sonnet-4-5-20250929）

        Returns:
            模型 ID 列表，按优先级排序；如果别名不存在或未启用则返回空列表
        """
        alias = await self.get_by_alias(db, alias_name)
        if not alias:
            return []
        return alias.model_ids or []

    async def get_mapped_models(
        self, db: AsyncSession, alias_name: str
    ) -> list[ModelConfig]:
        """根据别名获取映射的模型配置列表（按优先级排序）

        Args:
            db: 数据库会话
            alias_name: 别名（如 claude-sonnet-4-5-20250929）

        Returns:
            模型配置列表，按 model_ids 中的顺序排列（优先级从高到低）
        """
        model_ids = await self.get_mapped_model_ids(db, alias_name)
        if not model_ids:
            return []

        # 查询所有映射的模型
        stmt = select(ModelConfig).where(
            ModelConfig.id.in_(model_ids),
            ModelConfig.enabled == True  # noqa: E712
        )
        result = await db.execute(stmt)
        models = {m.id: m for m in result.scalars().all()}

        # 按 model_ids 中的顺序返回（保持优先级顺序）
        return [models[mid] for mid in model_ids if mid in models]

    async def get_list(
        self,
        *,
        alias_name: str | None = None,
        enabled: bool | None = None,
    ) -> Select:
        """获取别名列表"""
        filters = {}
        if alias_name is not None:
            filters['alias_name__like'] = f'%{alias_name}%'
        if enabled is not None:
            filters['enabled'] = enabled
        return await self.select_order('id', 'desc', **filters)

    async def get_all_enabled(self, db: AsyncSession) -> list[ModelAlias]:
        """获取所有启用的别名映射"""
        stmt = select(ModelAlias).where(ModelAlias.enabled == True)  # noqa: E712
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, db: AsyncSession, obj: 'CreateModelAliasParam') -> ModelAlias:
        """创建别名映射"""
        alias = ModelAlias(
            alias_name=obj.alias_name,
            model_ids=obj.model_ids,
            display_name=obj.display_name,
            description=obj.description,
            enabled=obj.enabled,
        )
        db.add(alias)
        await db.commit()
        await db.refresh(alias)
        return alias

    async def update(self, db: AsyncSession, pk: int, obj: 'UpdateModelAliasParam') -> int:
        """更新别名映射"""
        update_data = obj.model_dump(exclude_unset=True)
        count = await self.update_model(db, pk, update_data)
        await db.commit()
        return count

    async def delete(self, db: AsyncSession, pk: int) -> int:
        """删除别名映射"""
        count = await self.delete_model(db, pk)
        await db.commit()
        return count


model_alias_dao: CRUDModelAlias = CRUDModelAlias(ModelAlias)

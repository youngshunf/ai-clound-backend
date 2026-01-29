"""模型别名映射 Service

@author Ysf
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.crud.crud_model_alias import model_alias_dao
from backend.app.llm.crud.crud_model_config import model_config_dao
from backend.app.llm.model.model_alias import ModelAlias
from backend.app.llm.schema.model_alias import (
    CreateModelAliasParam,
    ModelAliasDetailResponse,
    UpdateModelAliasParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ModelAliasService:
    """模型别名映射服务"""

    @staticmethod
    async def get(db: AsyncSession, pk: int) -> ModelAlias:
        """获取模型别名"""
        alias = await model_alias_dao.get(db, pk)
        if not alias:
            raise errors.NotFoundError(msg='模型别名不存在')
        return alias

    @staticmethod
    async def get_detail(db: AsyncSession, pk: int) -> ModelAliasDetailResponse:
        """获取模型别名详情（包含映射的模型信息）"""
        alias = await model_alias_dao.get(db, pk)
        if not alias:
            raise errors.NotFoundError(msg='模型别名不存在')

        # 获取映射的模型详情
        mapped_models = []
        if alias.model_ids:
            for model_id in alias.model_ids:
                model = await model_config_dao.get(db, model_id)
                if model:
                    mapped_models.append({
                        'id': model.id,
                        'model_name': model.model_name,
                        'display_name': model.display_name,
                        'provider_name': model.provider.name if model.provider else None,
                    })

        return ModelAliasDetailResponse(
            id=alias.id,
            alias_name=alias.alias_name,
            model_ids=alias.model_ids or [],
            display_name=alias.display_name,
            description=alias.description,
            enabled=alias.enabled,
            mapped_models=mapped_models,
        )

    @staticmethod
    async def get_list(
        db: AsyncSession,
        *,
        alias_name: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """获取模型别名列表（分页）"""
        stmt = await model_alias_dao.get_list(alias_name=alias_name, enabled=enabled)
        page_data = await paging_data(db, stmt)
        return page_data

    @staticmethod
    async def create(db: AsyncSession, obj: CreateModelAliasParam) -> ModelAlias:
        """创建模型别名"""
        # 检查别名是否已存在
        existing = await model_alias_dao.get_by_alias(db, obj.alias_name)
        if existing:
            raise errors.ForbiddenError(msg='别名已存在')

        # 验证 model_ids 是否有效
        if obj.model_ids:
            for model_id in obj.model_ids:
                model = await model_config_dao.get(db, model_id)
                if not model:
                    raise errors.NotFoundError(msg=f'模型 ID {model_id} 不存在')

        return await model_alias_dao.create(db, obj)

    @staticmethod
    async def update(db: AsyncSession, pk: int, obj: UpdateModelAliasParam) -> int:
        """更新模型别名"""
        alias = await model_alias_dao.get(db, pk)
        if not alias:
            raise errors.NotFoundError(msg='模型别名不存在')

        # 检查别名是否重复
        if obj.alias_name and obj.alias_name != alias.alias_name:
            existing = await model_alias_dao.get_by_alias(db, obj.alias_name)
            if existing:
                raise errors.ForbiddenError(msg='别名已存在')

        # 验证 model_ids 是否有效
        if obj.model_ids is not None:
            for model_id in obj.model_ids:
                model = await model_config_dao.get(db, model_id)
                if not model:
                    raise errors.NotFoundError(msg=f'模型 ID {model_id} 不存在')

        return await model_alias_dao.update(db, pk, obj)

    @staticmethod
    async def delete(db: AsyncSession, pk: int) -> int:
        """删除模型别名"""
        alias = await model_alias_dao.get(db, pk)
        if not alias:
            raise errors.NotFoundError(msg='模型别名不存在')
        return await model_alias_dao.delete(db, pk)


model_alias_service = ModelAliasService()

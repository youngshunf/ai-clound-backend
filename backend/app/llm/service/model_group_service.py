"""模型组 Service

@author Ysf
@date 2025-12-28
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.llm.crud.crud_model_group import model_group_dao
from backend.app.llm.model.model_group import ModelGroup
from backend.app.llm.schema.model_group import (
    CreateModelGroupParam,
    GetModelGroupDetail,
    GetModelGroupList,
    UpdateModelGroupParam,
)
from backend.common.exception import errors
from backend.common.pagination import paging_data


class ModelGroupService:
    """模型组服务"""

    @staticmethod
    async def get(db: AsyncSession, pk: int) -> ModelGroup:
        """获取模型组"""
        group = await model_group_dao.get(db, pk)
        if not group:
            raise errors.NotFoundError(msg='模型组不存在')
        return group

    @staticmethod
    async def get_detail(db: AsyncSession, pk: int) -> GetModelGroupDetail:
        """获取模型组详情"""
        group = await model_group_dao.get(db, pk)
        if not group:
            raise errors.NotFoundError(msg='模型组不存在')
        return GetModelGroupDetail.model_validate(group)

    @staticmethod
    async def get_list(
        db: AsyncSession,
        *,
        name: str | None = None,
        model_type: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """获取模型组列表（分页）"""
        stmt = await model_group_dao.get_list(name=name, model_type=model_type, enabled=enabled)
        page_data = await paging_data(db, stmt)
        return page_data

    @staticmethod
    async def create(db: AsyncSession, obj: CreateModelGroupParam) -> None:
        """创建模型组"""
        existing = await model_group_dao.get_by_name(db, obj.name)
        if existing:
            raise errors.ForbiddenError(msg='模型组名称已存在')
        await model_group_dao.create(db, obj)

    @staticmethod
    async def update(db: AsyncSession, pk: int, obj: UpdateModelGroupParam) -> int:
        """更新模型组"""
        group = await model_group_dao.get(db, pk)
        if not group:
            raise errors.NotFoundError(msg='模型组不存在')
        if obj.name and obj.name != group.name:
            existing = await model_group_dao.get_by_name(db, obj.name)
            if existing:
                raise errors.ForbiddenError(msg='模型组名称已存在')
        return await model_group_dao.update(db, pk, obj)

    @staticmethod
    async def delete(db: AsyncSession, pk: int) -> int:
        """删除模型组"""
        group = await model_group_dao.get(db, pk)
        if not group:
            raise errors.NotFoundError(msg='模型组不存在')
        return await model_group_dao.delete(db, pk)


model_group_service = ModelGroupService()

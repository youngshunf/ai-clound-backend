"""模型组管理 API

@author Ysf
@date 2025-12-28
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.app.llm.schema.model_group import (
    CreateModelGroupParam,
    GetModelGroupDetail,
    GetModelGroupList,
    UpdateModelGroupParam,
)
from backend.app.llm.service.model_group_service import model_group_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取模型组列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_model_group_list(
    db: CurrentSession,
    name: Annotated[str | None, Query(description='组名称')] = None,
    model_type: Annotated[str | None, Query(description='模型类型')] = None,
    enabled: Annotated[bool | None, Query(description='是否启用')] = None,
) -> ResponseSchemaModel[PageData[GetModelGroupList]]:
    page_data = await model_group_service.get_list(db, name=name, model_type=model_type, enabled=enabled)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取模型组详情',
    dependencies=[DependsJwtAuth],
)
async def get_model_group_detail(db: CurrentSession, pk: int) -> ResponseSchemaModel[GetModelGroupDetail]:
    data = await model_group_service.get_detail(db, pk)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建模型组',
    dependencies=[
        Depends(RequestPermission('llm:model-group:add')),
        DependsRBAC,
    ],
)
async def create_model_group(db: CurrentSession, obj: CreateModelGroupParam) -> ResponseSchemaModel:
    await model_group_service.create(db, obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新模型组',
    dependencies=[
        Depends(RequestPermission('llm:model-group:edit')),
        DependsRBAC,
    ],
)
async def update_model_group(db: CurrentSession, pk: int, obj: UpdateModelGroupParam) -> ResponseSchemaModel:
    await model_group_service.update(db, pk, obj)
    return response_base.success()


@router.delete(
    '/{pk}',
    summary='删除模型组',
    dependencies=[
        Depends(RequestPermission('llm:model-group:del')),
        DependsRBAC,
    ],
)
async def delete_model_group(db: CurrentSession, pk: int) -> ResponseSchemaModel:
    await model_group_service.delete(db, pk)
    return response_base.success()

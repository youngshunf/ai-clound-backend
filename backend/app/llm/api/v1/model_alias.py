"""模型别名映射管理 API

@author Ysf
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.app.llm.schema.model_alias import (
    CreateModelAliasParam,
    ModelAliasDetailResponse,
    ModelAliasResponse,
    UpdateModelAliasParam,
)
from backend.app.llm.service.model_alias_service import model_alias_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取模型别名列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_model_alias_list(
    db: CurrentSession,
    alias_name: Annotated[str | None, Query(description='别名（模糊搜索）')] = None,
    enabled: Annotated[bool | None, Query(description='是否启用')] = None,
) -> ResponseSchemaModel[PageData[ModelAliasResponse]]:
    page_data = await model_alias_service.get_list(
        db, alias_name=alias_name, enabled=enabled
    )
    return response_base.success(data=page_data)


@router.get(
    '/{alias_id}',
    summary='获取模型别名详情',
    dependencies=[DependsJwtAuth],
)
async def get_model_alias_detail(
    db: CurrentSession,
    alias_id: int,
) -> ResponseSchemaModel[ModelAliasDetailResponse]:
    data = await model_alias_service.get_detail(db, alias_id)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建模型别名',
    dependencies=[
        Depends(RequestPermission('llm:model-alias:add')),
        DependsRBAC,
    ],
)
async def create_model_alias(
    db: CurrentSession,
    param: CreateModelAliasParam,
) -> ResponseSchemaModel:
    await model_alias_service.create(db, param)
    return response_base.success()


@router.put(
    '/{alias_id}',
    summary='更新模型别名',
    dependencies=[
        Depends(RequestPermission('llm:model-alias:edit')),
        DependsRBAC,
    ],
)
async def update_model_alias(
    db: CurrentSession,
    alias_id: int,
    param: UpdateModelAliasParam,
) -> ResponseSchemaModel:
    await model_alias_service.update(db, alias_id, param)
    return response_base.success()


@router.delete(
    '/{alias_id}',
    summary='删除模型别名',
    dependencies=[
        Depends(RequestPermission('llm:model-alias:del')),
        DependsRBAC,
    ],
)
async def delete_model_alias(
    db: CurrentSession,
    alias_id: int,
) -> ResponseSchemaModel:
    await model_alias_service.delete(db, alias_id)
    return response_base.success()

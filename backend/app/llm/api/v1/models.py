"""模型管理 API"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from backend.app.llm.schema.model_config import (
    CreateModelConfigParam,
    GetModelConfigDetail,
    GetModelConfigList,
    UpdateModelConfigParam,
)
from backend.app.llm.service.model_service import model_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '/available',
    summary='获取可用模型列表',
    description='公开接口，获取所有启用的模型列表（不含敏感信息）。返回格式与 agent-core ModelInfo 对应。',
)
async def get_available_models(db: CurrentSession) -> ResponseSchemaModel[dict]:
    data = await model_service.get_available_models(db)
    return response_base.success(data={'models': data})


@router.get(
    '',
    summary='获取模型配置列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_model_list(
    db: CurrentSession,
    provider_id: Annotated[int | None, Query(description='供应商 ID')] = None,
    model_type: Annotated[str | None, Query(description='模型类型')] = None,
    model_name: Annotated[str | None, Query(description='模型名称')] = None,
    enabled: Annotated[bool | None, Query(description='是否启用')] = None,
) -> ResponseSchemaModel[PageData[GetModelConfigList]]:
    page_data = await model_service.get_list(
        db,
        provider_id=provider_id,
        model_type=model_type,
        model_name=model_name,
        enabled=enabled,
    )
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取模型配置详情',
    dependencies=[DependsJwtAuth],
)
async def get_model_detail(db: CurrentSession, pk: int) -> ResponseSchemaModel[GetModelConfigDetail]:
    data = await model_service.get_detail(db, pk)
    return response_base.success(data=data)


@router.post(
    '',
    summary='创建模型配置',
    dependencies=[
        Depends(RequestPermission('llm:model:add')),
        DependsRBAC,
    ],
)
async def create_model(db: CurrentSession, obj: CreateModelConfigParam) -> ResponseSchemaModel:
    await model_service.create(db, obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新模型配置',
    dependencies=[
        Depends(RequestPermission('llm:model:edit')),
        DependsRBAC,
    ],
)
async def update_model(db: CurrentSession, pk: int, obj: UpdateModelConfigParam) -> ResponseSchemaModel:
    await model_service.update(db, pk, obj)
    return response_base.success()


@router.delete(
    '/{pk}',
    summary='删除模型配置',
    dependencies=[
        Depends(RequestPermission('llm:model:del')),
        DependsRBAC,
    ],
)
async def delete_model(db: CurrentSession, pk: int) -> ResponseSchemaModel:
    await model_service.delete(db, pk)
    return response_base.success()

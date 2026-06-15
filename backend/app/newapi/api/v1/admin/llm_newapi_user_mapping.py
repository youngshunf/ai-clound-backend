from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.newapi.schema.llm_newapi_user_mapping import (
    CreateLlmNewapiUserMappingParam,
    DeleteLlmNewapiUserMappingParam,
    GetLlmNewapiUserMappingDetail,
    UpdateLlmNewapiUserMappingParam,
)
from backend.app.newapi.service import llm_newapi_user_mapping_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取唤星用户与 new-api 用户映射详情', dependencies=[DependsJwtAuth], name='admin_get_llm_newapi_user_mapping')
async def get_llm_newapi_user_mapping(
    db: CurrentSession, pk: Annotated[int, Path(description='唤星用户与 new-api 用户映射 ID')]
) -> ResponseSchemaModel[GetLlmNewapiUserMappingDetail]:
    llm_newapi_user_mapping = await llm_newapi_user_mapping_service.get(db=db, pk=pk)
    return response_base.success(data=llm_newapi_user_mapping)


@router.get(
    '',
    summary='分页获取所有唤星用户与 new-api 用户映射',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_llm_newapi_user_mappings_paginated')
async def get_llm_newapi_user_mappings_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetLlmNewapiUserMappingDetail]]:
    page_data = await llm_newapi_user_mapping_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建唤星用户与 new-api 用户映射',
    dependencies=[
        Depends(RequestPermission('llm:newapi:user:mapping:add')),
        DependsRBAC,
    ],
)
async def create_llm_newapi_user_mapping(db: CurrentSessionTransaction, obj: CreateLlmNewapiUserMappingParam) -> ResponseModel:
    await llm_newapi_user_mapping_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新唤星用户与 new-api 用户映射',
    dependencies=[
        Depends(RequestPermission('llm:newapi:user:mapping:edit')),
        DependsRBAC,
    ],
)
async def update_llm_newapi_user_mapping(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='唤星用户与 new-api 用户映射 ID')], obj: UpdateLlmNewapiUserMappingParam
) -> ResponseModel:
    count = await llm_newapi_user_mapping_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除唤星用户与 new-api 用户映射',
    dependencies=[
        Depends(RequestPermission('llm:newapi:user:mapping:del')),
        DependsRBAC,
    ],
)
async def delete_llm_newapi_user_mappings(db: CurrentSessionTransaction, obj: DeleteLlmNewapiUserMappingParam) -> ResponseModel:
    count = await llm_newapi_user_mapping_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()

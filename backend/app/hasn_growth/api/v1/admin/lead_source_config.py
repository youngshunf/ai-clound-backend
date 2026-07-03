from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_growth.schema.lead_source_config import (
    CreateLeadSourceConfigParam,
    DeleteLeadSourceConfigParam,
    GetLeadSourceConfigDetail,
    UpdateLeadSourceConfigParam,
)
from backend.app.hasn_growth.service.lead_source_config_service import lead_source_config_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取AI lead automation source configuration详情', dependencies=[DependsJwtAuth], name='admin_get_lead_source_config')
async def get_lead_source_config(
    db: CurrentSession, pk: Annotated[int, Path(description='AI lead automation source configuration ID')]
) -> ResponseSchemaModel[GetLeadSourceConfigDetail]:
    lead_source_config = await lead_source_config_service.get(db=db, pk=pk)
    return response_base.success(data=lead_source_config)


@router.get(
    '',
    summary='分页获取所有AI lead automation source configuration',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_lead_source_configs_paginated')
async def get_lead_source_configs_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetLeadSourceConfigDetail]]:
    page_data = await lead_source_config_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建AI lead automation source configuration',
    dependencies=[
        Depends(RequestPermission('lead:source:config:add')),
        DependsRBAC,
    ],
)
async def create_lead_source_config(db: CurrentSessionTransaction, obj: CreateLeadSourceConfigParam) -> ResponseModel:
    await lead_source_config_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新AI lead automation source configuration',
    dependencies=[
        Depends(RequestPermission('lead:source:config:edit')),
        DependsRBAC,
    ],
)
async def update_lead_source_config(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='AI lead automation source configuration ID')], obj: UpdateLeadSourceConfigParam
) -> ResponseModel:
    count = await lead_source_config_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除AI lead automation source configuration',
    dependencies=[
        Depends(RequestPermission('lead:source:config:del')),
        DependsRBAC,
    ],
)
async def delete_lead_source_configs(db: CurrentSessionTransaction, obj: DeleteLeadSourceConfigParam) -> ResponseModel:
    count = await lead_source_config_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()

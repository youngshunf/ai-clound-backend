from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_growth.schema.lead_collection_job import (
    CreateLeadCollectionJobParam,
    DeleteLeadCollectionJobParam,
    GetLeadCollectionJobDetail,
    UpdateLeadCollectionJobParam,
)
from backend.app.hasn_growth.service.lead_collection_job_service import lead_collection_job_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取AI lead automation collection job详情', dependencies=[DependsJwtAuth], name='admin_get_lead_collection_job')
async def get_lead_collection_job(
    db: CurrentSession, pk: Annotated[int, Path(description='AI lead automation collection job ID')]
) -> ResponseSchemaModel[GetLeadCollectionJobDetail]:
    lead_collection_job = await lead_collection_job_service.get(db=db, pk=pk)
    return response_base.success(data=lead_collection_job)


@router.get(
    '',
    summary='分页获取所有AI lead automation collection job',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_lead_collection_jobs_paginated')
async def get_lead_collection_jobs_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetLeadCollectionJobDetail]]:
    page_data = await lead_collection_job_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建AI lead automation collection job',
    dependencies=[
        Depends(RequestPermission('lead:collection:job:add')),
        DependsRBAC,
    ],
)
async def create_lead_collection_job(db: CurrentSessionTransaction, obj: CreateLeadCollectionJobParam) -> ResponseModel:
    await lead_collection_job_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新AI lead automation collection job',
    dependencies=[
        Depends(RequestPermission('lead:collection:job:edit')),
        DependsRBAC,
    ],
)
async def update_lead_collection_job(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='AI lead automation collection job ID')], obj: UpdateLeadCollectionJobParam
) -> ResponseModel:
    count = await lead_collection_job_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除AI lead automation collection job',
    dependencies=[
        Depends(RequestPermission('lead:collection:job:del')),
        DependsRBAC,
    ],
)
async def delete_lead_collection_jobs(db: CurrentSessionTransaction, obj: DeleteLeadCollectionJobParam) -> ResponseModel:
    count = await lead_collection_job_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()

"""AI lead automation collection job - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn_growth.schema.lead_collection_job import (
    CreateLeadCollectionJobParam,
    GetLeadCollectionJobDetail,
    UpdateLeadCollectionJobParam,
)
from backend.app.hasn_growth.service.lead_collection_job_service import lead_collection_job_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的AI lead automation collection job列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_lead_collection_jobs(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetLeadCollectionJobDetail]]:
    page_data = await lead_collection_job_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建AI lead automation collection job',
    dependencies=[DependsJwtAuth],
)
async def create_my_lead_collection_job(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateLeadCollectionJobParam,
) -> ResponseModel:
    await lead_collection_job_service.create(db=db, obj=obj)
    return response_base.success()


@router.get(
    '/{pk}',
    summary='获取AI lead automation collection job详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_lead_collection_job(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='AI lead automation collection job ID')],
) -> ResponseSchemaModel[GetLeadCollectionJobDetail]:
    lead_collection_job = await lead_collection_job_service.get(db=db, pk=pk)
    if lead_collection_job.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该AI lead automation collection job')
    return response_base.success(data=lead_collection_job)


@router.put(
    '/{pk}',
    summary='更新AI lead automation collection job',
    dependencies=[DependsJwtAuth],
)
async def update_my_lead_collection_job(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='AI lead automation collection job ID')],
    obj: UpdateLeadCollectionJobParam,
) -> ResponseModel:
    lead_collection_job = await lead_collection_job_service.get(db=db, pk=pk)
    if getattr(lead_collection_job, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该AI lead automation collection job')
    count = await lead_collection_job_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除AI lead automation collection job',
    dependencies=[DependsJwtAuth],
)
async def delete_my_lead_collection_job(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='AI lead automation collection job ID')],
) -> ResponseModel:
    user_id = request.user.id
    lead_collection_job = await lead_collection_job_service.get(db=db, pk=pk)
    if lead_collection_job.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该AI lead automation collection job')
    from backend.app.hasn_growth.schema.lead_collection_job import DeleteLeadCollectionJobParam
    count = await lead_collection_job_service.delete(db=db, obj=DeleteLeadCollectionJobParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()

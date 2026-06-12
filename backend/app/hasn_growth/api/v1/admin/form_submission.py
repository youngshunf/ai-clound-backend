from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_growth.schema.form_submission import (
    CreateFormSubmissionParam,
    DeleteFormSubmissionParam,
    GetFormSubmissionDetail,
    UpdateFormSubmissionParam,
)
from backend.app.hasn_growth.service.form_submission_service import form_submission_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取获客落地页表单回流（inbound 线索缓冲区）详情', dependencies=[DependsJwtAuth], name='admin_get_form_submission')
async def get_form_submission(
    db: CurrentSession, pk: Annotated[int, Path(description='获客落地页表单回流（inbound 线索缓冲区） ID')]
) -> ResponseSchemaModel[GetFormSubmissionDetail]:
    form_submission = await form_submission_service.get(db=db, pk=pk)
    return response_base.success(data=form_submission)


@router.get(
    '',
    summary='分页获取所有获客落地页表单回流（inbound 线索缓冲区）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='admin_get_form_submission_paginated',
)
async def get_form_submission_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetFormSubmissionDetail]]:
    page_data = await form_submission_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建获客落地页表单回流（inbound 线索缓冲区）',
    dependencies=[
        Depends(RequestPermission('form:submission:add')),
        DependsRBAC,
    ],
    name='admin_create_form_submission',
)
async def create_form_submission(db: CurrentSessionTransaction, obj: CreateFormSubmissionParam) -> ResponseModel:
    await form_submission_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新获客落地页表单回流（inbound 线索缓冲区）',
    dependencies=[
        Depends(RequestPermission('form:submission:edit')),
        DependsRBAC,
    ],
    name='admin_update_form_submission',
)
async def update_form_submission(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='获客落地页表单回流（inbound 线索缓冲区） ID')], obj: UpdateFormSubmissionParam
) -> ResponseModel:
    count = await form_submission_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除获客落地页表单回流（inbound 线索缓冲区）',
    dependencies=[
        Depends(RequestPermission('form:submission:del')),
        DependsRBAC,
    ],
    name='admin_delete_form_submission',
)
async def delete_form_submission(db: CurrentSessionTransaction, obj: DeleteFormSubmissionParam) -> ResponseModel:
    count = await form_submission_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()

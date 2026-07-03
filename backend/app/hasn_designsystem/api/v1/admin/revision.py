from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_designsystem.schema.revision import (
    CreateRevisionParam,
    DeleteRevisionParam,
    GetRevisionDetail,
    UpdateRevisionParam,
)
from backend.app.hasn_designsystem.service.revision_service import revision_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取演示文稿版本快照（云端权威历史）详情', dependencies=[DependsJwtAuth], name='hasn_designsystem_admin_get_revision')
async def get_revision(
    db: CurrentSession, pk: Annotated[int, Path(description='演示文稿版本快照（云端权威历史） ID')]
) -> ResponseSchemaModel[GetRevisionDetail]:
    revision = await revision_service.get(db=db, pk=pk)
    return response_base.success(data=revision)


@router.get(
    '',
    summary='分页获取所有演示文稿版本快照（云端权威历史）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_designsystem_admin_get_revision_paginated',
)
async def get_revision_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetRevisionDetail]]:
    page_data = await revision_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建演示文稿版本快照（云端权威历史）',
    dependencies=[
        Depends(RequestPermission('revision:add')),
        DependsRBAC,
    ],
    name='hasn_designsystem_admin_create_revision',
)
async def create_revision(db: CurrentSessionTransaction, obj: CreateRevisionParam) -> ResponseModel:
    await revision_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新演示文稿版本快照（云端权威历史）',
    dependencies=[
        Depends(RequestPermission('revision:edit')),
        DependsRBAC,
    ],
    name='hasn_designsystem_admin_update_revision',
)
async def update_revision(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='演示文稿版本快照（云端权威历史） ID')], obj: UpdateRevisionParam
) -> ResponseModel:
    count = await revision_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除演示文稿版本快照（云端权威历史）',
    dependencies=[
        Depends(RequestPermission('revision:del')),
        DependsRBAC,
    ],
    name='hasn_designsystem_admin_delete_revision',
)
async def delete_revision(db: CurrentSessionTransaction, obj: DeleteRevisionParam) -> ResponseModel:
    count = await revision_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()

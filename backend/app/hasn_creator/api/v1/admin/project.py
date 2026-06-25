from typing import Annotated

from fastapi import APIRouter, Depends, Path

from backend.app.hasn_creator.schema.project import (
    CreateProjectParam,
    DeleteProjectParam,
    GetProjectDetail,
    UpdateProjectParam,
)
from backend.app.hasn_creator.service.project_service import project_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度详情', dependencies=[DependsJwtAuth], name='hasn_creator_admin_get_project')
async def get_project(
    db: CurrentSession, pk: Annotated[int, Path(description='运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID')]
) -> ResponseSchemaModel[GetProjectDetail]:
    project = await project_service.get(db=db, pk=pk)
    return response_base.success(data=project)


@router.get(
    '',
    summary='分页获取所有运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_creator_admin_get_project_paginated',
)
async def get_project_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetProjectDetail]]:
    page_data = await project_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度',
    dependencies=[
        Depends(RequestPermission('project:add')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_create_project',
)
async def create_project(db: CurrentSessionTransaction, obj: CreateProjectParam) -> ResponseModel:
    await project_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度',
    dependencies=[
        Depends(RequestPermission('project:edit')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_update_project',
)
async def update_project(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID')], obj: UpdateProjectParam
) -> ResponseModel:
    count = await project_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度',
    dependencies=[
        Depends(RequestPermission('project:del')),
        DependsRBAC,
    ],
    name='hasn_creator_admin_delete_project',
)
async def delete_project(db: CurrentSessionTransaction, obj: DeleteProjectParam) -> ResponseModel:
    count = await project_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()

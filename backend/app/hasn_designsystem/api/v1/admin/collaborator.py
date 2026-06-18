from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.hasn_designsystem.schema.collaborator import (
    CreateCollaboratorParam,
    DeleteCollaboratorParam,
    GetCollaboratorDetail,
    UpdateCollaboratorParam,
)
from backend.app.hasn_designsystem.service.collaborator_service import collaborator_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取设计系统协作分身绑定（对齐 DECKBIND）详情', dependencies=[DependsJwtAuth], name='hasn_designsystem_admin_get_collaborator')
async def get_collaborator(
    db: CurrentSession, pk: Annotated[int, Path(description='设计系统协作分身绑定（对齐 DECKBIND） ID')]
) -> ResponseSchemaModel[GetCollaboratorDetail]:
    collaborator = await collaborator_service.get(db=db, pk=pk)
    return response_base.success(data=collaborator)


@router.get(
    '',
    summary='分页获取所有设计系统协作分身绑定（对齐 DECKBIND）',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
    name='hasn_designsystem_admin_get_collaborator_paginated',
)
async def get_collaborator_paginated(db: CurrentSession) -> ResponseSchemaModel[PageData[GetCollaboratorDetail]]:
    page_data = await collaborator_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建设计系统协作分身绑定（对齐 DECKBIND）',
    dependencies=[
        Depends(RequestPermission('collaborator:add')),
        DependsRBAC,
    ],
    name='hasn_designsystem_admin_create_collaborator',
)
async def create_collaborator(db: CurrentSessionTransaction, obj: CreateCollaboratorParam) -> ResponseModel:
    await collaborator_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新设计系统协作分身绑定（对齐 DECKBIND）',
    dependencies=[
        Depends(RequestPermission('collaborator:edit')),
        DependsRBAC,
    ],
    name='hasn_designsystem_admin_update_collaborator',
)
async def update_collaborator(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='设计系统协作分身绑定（对齐 DECKBIND） ID')], obj: UpdateCollaboratorParam
) -> ResponseModel:
    count = await collaborator_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除设计系统协作分身绑定（对齐 DECKBIND）',
    dependencies=[
        Depends(RequestPermission('collaborator:del')),
        DependsRBAC,
    ],
    name='hasn_designsystem_admin_delete_collaborator',
)
async def delete_collaborator(db: CurrentSessionTransaction, obj: DeleteCollaboratorParam) -> ResponseModel:
    count = await collaborator_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()

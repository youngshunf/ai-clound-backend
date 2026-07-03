"""HASN 群成员 - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.hasn.schema.hasn_group_members import (
    CreateHasnGroupMembersParam,
    GetHasnGroupMembersDetail,
    UpdateHasnGroupMembersParam,
)
from backend.app.hasn.service.hasn_group_members_service import hasn_group_members_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的HASN 群成员列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_hasn_group_memberss(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetHasnGroupMembersDetail]]:
    page_data = await hasn_group_members_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 群成员',
    dependencies=[DependsJwtAuth],
)
async def create_my_hasn_group_members(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnGroupMembersParam,
) -> ResponseModel:
    result = await hasn_group_members_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取HASN 群成员详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_hasn_group_members(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='HASN 群成员 ID')],
) -> ResponseSchemaModel[GetHasnGroupMembersDetail]:
    hasn_group_members = await hasn_group_members_service.get(db=db, pk=pk)
    if hasn_group_members.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该HASN 群成员')
    return response_base.success(data=hasn_group_members)


@router.put(
    '/{pk}',
    summary='更新HASN 群成员',
    dependencies=[DependsJwtAuth],
)
async def update_my_hasn_group_members(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='HASN 群成员 ID')],
    obj: UpdateHasnGroupMembersParam,
) -> ResponseModel:
    user_id = request.user.id
    hasn_group_members = await hasn_group_members_service.get(db=db, pk=pk)
    if hasn_group_members.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该HASN 群成员')
    count = await hasn_group_members_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除HASN 群成员',
    dependencies=[DependsJwtAuth],
)
async def delete_my_hasn_group_members(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='HASN 群成员 ID')],
) -> ResponseModel:
    user_id = request.user.id
    hasn_group_members = await hasn_group_members_service.get(db=db, pk=pk)
    if hasn_group_members.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该HASN 群成员')
    from backend.app.hasn.schema.hasn_group_members import DeleteHasnGroupMembersParam
    count = await hasn_group_members_service.delete(db=db, obj=DeleteHasnGroupMembersParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()

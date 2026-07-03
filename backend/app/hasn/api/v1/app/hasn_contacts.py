"""HASN 联系人关系 - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated, Any

from fastapi import APIRouter, Path, Request

from backend.app.hasn.schema.hasn_contacts import (
    CreateHasnContactsParam,
    UpdateHasnContactsParam,
)
from backend.app.hasn.service.hasn_contacts_service import hasn_contacts_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的HASN 联系人关系列表',
    dependencies=[DependsJwtAuth, DependsPagination],
)
async def get_my_hasn_contactss(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[dict[str, Any]]]:
    user_id = request.user.id
    page_data = await hasn_contacts_service.get_list(db=db, user_id=user_id)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建HASN 联系人关系',
    dependencies=[DependsJwtAuth],
)
async def create_my_hasn_contacts(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateHasnContactsParam,
) -> ResponseModel:
    result = await hasn_contacts_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取HASN 联系人关系详情',
    dependencies=[DependsJwtAuth],
)
async def get_my_hasn_contacts(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='HASN 联系人关系 ID')],
) -> ResponseSchemaModel[dict[str, Any]]:
    hasn_contacts = await hasn_contacts_service.get(db=db, pk=pk)

    # 权限检查：通过 owner_id 查询对应的 user_id
    from sqlalchemy import select

    from backend.app.hasn.model.hasn_humans import HasnHumans

    owner_id = hasn_contacts.get("owner_id")
    result = await db.execute(select(HasnHumans.user_id).where(HasnHumans.hasn_id == owner_id))
    owner_user_id = result.scalar_one_or_none()

    if owner_user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该HASN 联系人关系')

    return response_base.success(data=hasn_contacts)


@router.put(
    '/{pk}',
    summary='更新HASN 联系人关系',
    dependencies=[DependsJwtAuth],
)
async def update_my_hasn_contacts(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='HASN 联系人关系 ID')],
    obj: UpdateHasnContactsParam,
) -> ResponseModel:
    user_id = request.user.id
    hasn_contacts = await hasn_contacts_service.get(db=db, pk=pk)
    if hasn_contacts.user_id != user_id:
        raise errors.ForbiddenError(msg='无权修改该HASN 联系人关系')
    count = await hasn_contacts_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除HASN 联系人关系',
    dependencies=[DependsJwtAuth],
)
async def delete_my_hasn_contacts(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='HASN 联系人关系 ID')],
) -> ResponseModel:
    user_id = request.user.id
    hasn_contacts = await hasn_contacts_service.get(db=db, pk=pk)
    if hasn_contacts.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该HASN 联系人关系')
    from backend.app.hasn.schema.hasn_contacts import DeleteHasnContactsParam
    count = await hasn_contacts_service.delete(db=db, obj=DeleteHasnContactsParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()

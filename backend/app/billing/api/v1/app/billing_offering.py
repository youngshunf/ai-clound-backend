"""商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.billing.schema.billing_offering import (
    CreateBillingOfferingParam,
    GetBillingOfferingDetail,
    UpdateBillingOfferingParam,
)
from backend.app.billing.service.billing_offering_service import billing_offering_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='billing_app_get_my_billing_offering',
)
async def get_my_billing_offering(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetBillingOfferingDetail]]:
    page_data = await billing_offering_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）',
    dependencies=[DependsJwtAuth],
    name='billing_app_create_my_billing_offering',
)
async def create_my_billing_offering(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateBillingOfferingParam,
) -> ResponseModel:
    result = await billing_offering_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）详情',
    dependencies=[DependsJwtAuth],
    name='billing_app_get_my_billing_offering_detail',
)
async def get_my_billing_offering_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID')],
) -> ResponseSchemaModel[GetBillingOfferingDetail]:
    billing_offering = await billing_offering_service.get(db=db, pk=pk)
    if billing_offering.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）')
    return response_base.success(data=billing_offering)


@router.put(
    '/{pk}',
    summary='更新商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）',
    dependencies=[DependsJwtAuth],
    name='billing_app_update_my_billing_offering',
)
async def update_my_billing_offering(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID')],
    obj: UpdateBillingOfferingParam,
) -> ResponseModel:
    billing_offering = await billing_offering_service.get(db=db, pk=pk)
    if getattr(billing_offering, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）')
    count = await billing_offering_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）',
    dependencies=[DependsJwtAuth],
    name='billing_app_delete_my_billing_offering',
)
async def delete_my_billing_offering(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID')],
) -> ResponseModel:
    user_id = request.user.id
    billing_offering = await billing_offering_service.get(db=db, pk=pk)
    if billing_offering.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）')
    from backend.app.billing.schema.billing_offering import DeleteBillingOfferingParam
    count = await billing_offering_service.delete(db=db, obj=DeleteBillingOfferingParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()

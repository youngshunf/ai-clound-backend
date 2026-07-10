"""商品档位（价格+配额快照+试用/宽限策略） - 用户端 API

认证方式: DependsJwtAuth（仅当前登录用户）
数据隔离: 通过 request.user.id 限制为用户自己的数据
"""
from typing import Annotated

from fastapi import APIRouter, Path, Request

from backend.app.billing.schema.billing_plan import (
    CreateBillingPlanParam,
    GetBillingPlanDetail,
    UpdateBillingPlanParam,
)
from backend.app.billing.service.billing_plan_service import billing_plan_service
from backend.common.exception import errors
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get(
    '',
    summary='获取我的商品档位（价格+配额快照+试用/宽限策略）列表',
    dependencies=[DependsJwtAuth, DependsPagination],
    name='billing_app_get_my_billing_plan',
)
async def get_my_billing_plan(
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetBillingPlanDetail]]:
    page_data = await billing_plan_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建商品档位（价格+配额快照+试用/宽限策略）',
    dependencies=[DependsJwtAuth],
    name='billing_app_create_my_billing_plan',
)
async def create_my_billing_plan(
    request: Request,
    db: CurrentSessionTransaction,
    obj: CreateBillingPlanParam,
) -> ResponseModel:
    result = await billing_plan_service.create(db=db, obj=obj)
    return response_base.success(data=result)


@router.get(
    '/{pk}',
    summary='获取商品档位（价格+配额快照+试用/宽限策略）详情',
    dependencies=[DependsJwtAuth],
    name='billing_app_get_my_billing_plan_detail',
)
async def get_my_billing_plan_detail(
    request: Request,
    db: CurrentSession,
    pk: Annotated[int, Path(description='商品档位（价格+配额快照+试用/宽限策略） ID')],
) -> ResponseSchemaModel[GetBillingPlanDetail]:
    billing_plan = await billing_plan_service.get(db=db, pk=pk)
    if billing_plan.user_id != request.user.id:
        raise errors.ForbiddenError(msg='无权访问该商品档位（价格+配额快照+试用/宽限策略）')
    return response_base.success(data=billing_plan)


@router.put(
    '/{pk}',
    summary='更新商品档位（价格+配额快照+试用/宽限策略）',
    dependencies=[DependsJwtAuth],
    name='billing_app_update_my_billing_plan',
)
async def update_my_billing_plan(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='商品档位（价格+配额快照+试用/宽限策略） ID')],
    obj: UpdateBillingPlanParam,
) -> ResponseModel:
    billing_plan = await billing_plan_service.get(db=db, pk=pk)
    if getattr(billing_plan, 'user_id', request.user.id) != request.user.id:
        raise errors.ForbiddenError(msg='无权修改该商品档位（价格+配额快照+试用/宽限策略）')
    count = await billing_plan_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除商品档位（价格+配额快照+试用/宽限策略）',
    dependencies=[DependsJwtAuth],
    name='billing_app_delete_my_billing_plan',
)
async def delete_my_billing_plan(
    request: Request,
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='商品档位（价格+配额快照+试用/宽限策略） ID')],
) -> ResponseModel:
    user_id = request.user.id
    billing_plan = await billing_plan_service.get(db=db, pk=pk)
    if billing_plan.user_id != user_id:
        raise errors.ForbiddenError(msg='无权删除该商品档位（价格+配额快照+试用/宽限策略）')
    from backend.app.billing.schema.billing_plan import DeleteBillingPlanParam
    count = await billing_plan_service.delete(db=db, obj=DeleteBillingPlanParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()

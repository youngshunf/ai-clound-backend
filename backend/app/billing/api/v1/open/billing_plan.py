"""商品档位（价格+配额快照+试用/宽限策略） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.billing.schema.billing_plan import GetBillingPlanDetail
from backend.app.billing.service.billing_plan_service import billing_plan_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取商品档位（价格+配额快照+试用/宽限策略）列表',
    dependencies=[DependsPagination],
    name='billing_open_get_billing_plan',
)
async def get_billing_plan(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetBillingPlanDetail]]:
    page_data = await billing_plan_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取商品档位（价格+配额快照+试用/宽限策略）详情',
    name='billing_open_get_billing_plan_detail',
)
async def get_billing_plan_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='商品档位（价格+配额快照+试用/宽限策略） ID')],
) -> ResponseSchemaModel[GetBillingPlanDetail]:
    billing_plan = await billing_plan_service.get(db=db, pk=pk)
    return response_base.success(data=billing_plan)

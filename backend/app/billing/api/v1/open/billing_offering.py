"""商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.billing.schema.billing_offering import GetBillingOfferingDetail
from backend.app.billing.service.billing_offering_service import billing_offering_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）列表',
    dependencies=[DependsPagination],
    name='billing_open_get_billing_offering',
)
async def get_billing_offering(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetBillingOfferingDetail]]:
    page_data = await billing_offering_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）详情',
    name='billing_open_get_billing_offering_detail',
)
async def get_billing_offering_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位） ID')],
) -> ResponseSchemaModel[GetBillingOfferingDetail]:
    billing_offering = await billing_offering_service.get(db=db, pk=pk)
    return response_base.success(data=billing_offering)

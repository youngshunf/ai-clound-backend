"""Admin API — 签约管理"""

from typing import Annotated

from fastapi import APIRouter, Path, Query

from backend.app.billing.schema.pay_contract import GetPayContractDetail
from backend.app.billing.service.pay_contract_service import pay_contract_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='分页获取签约列表（管理端）',
    dependencies=[DependsJwtAuth, DependsPagination],
 name='admin_get_contracts_paginated')
async def get_contracts_paginated(
    db: CurrentSession,
    user_id: Annotated[int | None, Query(description='按用户筛选')] = None,
    status: Annotated[int | None, Query(description='按状态筛选')] = None,
) -> ResponseSchemaModel[PageData[GetPayContractDetail]]:
    page_data = await pay_contract_service.get_list(db=db, user_id=user_id, status=status)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取签约详情',
    dependencies=[DependsJwtAuth],
 name='admin_get_contract_detail')
async def get_contract_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='签约 ID')],
) -> ResponseSchemaModel[GetPayContractDetail]:
    contract = await pay_contract_service.get(db=db, pk=pk)
    return response_base.success(data=contract)

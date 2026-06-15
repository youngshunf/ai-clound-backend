"""平台账号（1:N project）；同一项目多平台真实账号 - 公开 API

认证方式: 无（公开接口，无需登录）
"""
from typing import Annotated

from fastapi import APIRouter, Path

from backend.app.hasn_creator.schema.account import GetAccountDetail
from backend.app.hasn_creator.service.account_service import account_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='获取平台账号（1:N project）；同一项目多平台真实账号列表',
    dependencies=[DependsPagination],
    name='hasn_creator_open_get_account',
)
async def get_account(
    db: CurrentSession,
) -> ResponseSchemaModel[PageData[GetAccountDetail]]:
    page_data = await account_service.get_list(db=db)
    return response_base.success(data=page_data)


@router.get(
    '/{pk}',
    summary='获取平台账号（1:N project）；同一项目多平台真实账号详情',
    name='hasn_creator_open_get_account_detail',
)
async def get_account_detail(
    db: CurrentSession,
    pk: Annotated[int, Path(description='平台账号（1:N project）；同一项目多平台真实账号 ID')],
) -> ResponseSchemaModel[GetAccountDetail]:
    account = await account_service.get(db=db, pk=pk)
    return response_base.success(data=account)

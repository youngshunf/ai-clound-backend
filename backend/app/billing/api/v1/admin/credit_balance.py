from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from backend.app.billing.schema.user_credit_balance import (
    CreateUserCreditBalanceParam,
    DeleteUserCreditBalanceParam,
    GetUserCreditBalanceDetail,
    UpdateUserCreditBalanceParam,
)
from backend.app.billing.schema.grant_credits import GrantCreditsParam, GrantCreditsResult
from backend.app.billing.service.user_credit_balance_service import user_credit_balance_service
from backend.app.billing.service.credit_service import credit_service
from backend.common.pagination import DependsPagination, PageData
from backend.common.response.response_schema import ResponseModel, ResponseSchemaModel, response_base
from backend.common.security.jwt import DependsJwtAuth
from backend.common.security.permission import RequestPermission
from backend.common.security.rbac import DependsRBAC
from backend.database.db import CurrentSession, CurrentSessionTransaction

router = APIRouter()


@router.get('/{pk}', summary='获取用户积分余额详情', dependencies=[DependsJwtAuth], name='admin_get_user_credit_balance')
async def get_user_credit_balance(
    db: CurrentSession, pk: Annotated[int, Path(description='用户积分余额 ID')]
) -> ResponseSchemaModel[GetUserCreditBalanceDetail]:
    user_credit_balance = await user_credit_balance_service.get(db=db, pk=pk)
    return response_base.success(data=user_credit_balance)


@router.get(
    '',
    summary='分页获取所有用户积分余额',
    dependencies=[
        DependsJwtAuth,
        DependsPagination,
    ],
 name='admin_get_user_credit_balances_paginated')
async def get_user_credit_balances_paginated(
    db: CurrentSession,
    user_keyword: Annotated[str | None, Query(description='用户昵称/手机号搜索')] = None,
    credit_type: Annotated[str | None, Query(description='积分类型')] = None,
    expires_at: Annotated[list[date] | None, Query(description='过期时间范围')] = None,
    granted_at: Annotated[list[date] | None, Query(description='发放时间范围')] = None,
    source_type: Annotated[str | None, Query(description='来源类型')] = None,
    source_reference_id: Annotated[str | None, Query(description='关联订单号')] = None,
) -> ResponseSchemaModel[PageData[GetUserCreditBalanceDetail]]:
    page_data = await user_credit_balance_service.get_list(
        db=db,
        user_keyword=user_keyword,
        credit_type=credit_type,
        expires_at=expires_at,
        granted_at=granted_at,
        source_type=source_type,
        source_reference_id=source_reference_id,
    )
    return response_base.success(data=page_data)


@router.post(
    '',
    summary='创建用户积分余额',
    dependencies=[
        Depends(RequestPermission('user:credit:balance:add')),
        DependsRBAC,
    ],
)
async def create_user_credit_balance(db: CurrentSessionTransaction, obj: CreateUserCreditBalanceParam) -> ResponseModel:
    await user_credit_balance_service.create(db=db, obj=obj)
    return response_base.success()


@router.put(
    '/{pk}',
    summary='更新用户积分余额',
    dependencies=[
        Depends(RequestPermission('user:credit:balance:edit')),
        DependsRBAC,
    ],
)
async def update_user_credit_balance(
    db: CurrentSessionTransaction,
    pk: Annotated[int, Path(description='用户积分余额 ID')],
    obj: UpdateUserCreditBalanceParam,
) -> ResponseModel:
    count = await user_credit_balance_service.update(db=db, pk=pk, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '/{pk}',
    summary='删除用户积分余额',
    dependencies=[
        Depends(RequestPermission('user:credit:balance:del')),
        DependsRBAC,
    ],
)
async def delete_user_credit_balance(
    db: CurrentSessionTransaction, pk: Annotated[int, Path(description='用户积分余额 ID')]
) -> ResponseModel:
    count = await user_credit_balance_service.delete(db=db, obj=DeleteUserCreditBalanceParam(pks=[pk]))
    if count > 0:
        return response_base.success()
    return response_base.fail()


@router.delete(
    '',
    summary='批量删除用户积分余额',
    dependencies=[
        Depends(RequestPermission('user:credit:balance:del')),
        DependsRBAC,
    ],
)
async def delete_user_credit_balances(
    db: CurrentSessionTransaction, obj: DeleteUserCreditBalanceParam
) -> ResponseModel:
    count = await user_credit_balance_service.delete(db=db, obj=obj)
    if count > 0:
        return response_base.success()
    return response_base.fail()



@router.post(
    '/grant',
    summary='管理员赠送积分',
    dependencies=[
        Depends(RequestPermission('user:credit:balance:add')),
        DependsRBAC,
    ],
)
async def grant_credits(db: CurrentSessionTransaction, obj: GrantCreditsParam) -> ResponseSchemaModel[GrantCreditsResult]:
    """
    管理员批量赠送积分给用户

    - 支持单用户或多用户批量赠送
    - 积分类型自动标记为 official_grant（官方赠送）
    - 记录完整的积分交易日志
    """
    from decimal import Decimal
    from backend.utils.timezone import timezone

    success_count = 0
    failed_count = 0
    details = []

    for user_id in obj.user_ids:
        try:
            await credit_service.add_credits(
                db=db,
                user_id=user_id,
                credits=obj.amount,
                transaction_type='official_grant',
                reference_type='system',
                description=obj.description or '管理员赠送积分',
                is_purchased=False,
                expires_at=obj.expires_at,
            )
            success_count += 1
            details.append({'user_id': user_id, 'status': 'success', 'credits': float(obj.amount)})
        except Exception as e:
            failed_count += 1
            details.append({'user_id': user_id, 'status': 'failed', 'error': str(e)})

    result = GrantCreditsResult(
        success_count=success_count,
        failed_count=failed_count,
        total_credits=obj.amount * success_count,
        details=details,
    )
    return response_base.success(data=result)

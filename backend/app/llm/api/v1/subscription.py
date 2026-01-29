"""订阅信息 API - 用于桌面端查询用户订阅和积分信息
@author Ysf
"""

from typing import Annotated

from fastapi import APIRouter, Header, Query
from pydantic import BaseModel
from decimal import Decimal
from datetime import datetime

from backend.app.llm.service.api_key_service import api_key_service
from backend.app.user_tier.service.credit_service import credit_service
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.database.db import CurrentSession

router = APIRouter()


class SubscriptionInfoResponse(BaseModel):
    """订阅信息响应"""
    user_id: int
    tier: str
    tier_display_name: str | None = None
    monthly_credits: Decimal
    current_credits: Decimal
    used_credits: Decimal
    purchased_credits: Decimal
    billing_cycle_start: datetime
    billing_cycle_end: datetime
    status: str
    auto_renew: bool


class CreditUsageSummary(BaseModel):
    """积分使用统计"""
    total_credits_used: Decimal
    total_transactions: int
    usage_by_model: list[dict]


class CreditTransactionItem(BaseModel):
    """积分交易记录"""
    id: int
    transaction_type: str
    credits: Decimal
    balance_before: Decimal
    balance_after: Decimal
    reference_type: str | None
    description: str | None
    created_at: datetime


class CreditTransactionListResponse(BaseModel):
    """积分交易记录列表响应"""
    items: list[CreditTransactionItem]
    total: int
    page: int
    page_size: int


@router.get(
    '/info',
    summary='获取当前用户订阅信息',
    description='获取当前登录用户的订阅和积分信息，使用 x-api-key 认证',
)
async def get_subscription_info(
    db: CurrentSession,
    x_api_key: Annotated[str, Header(alias='x-api-key', description='LLM API Key (sk-cf-xxx)')],
) -> ResponseSchemaModel[SubscriptionInfoResponse]:
    """获取订阅信息"""
    # 从 API Key 获取用户信息
    api_key_obj = await api_key_service.verify_api_key(db, x_api_key)
    user_id = api_key_obj.user_id
    
    # 获取或创建订阅
    subscription = await credit_service.get_or_create_subscription(db, user_id)
    
    # 获取订阅等级信息
    from backend.app.user_tier.crud.crud_subscription_tier import subscription_tier_dao
    tier_info = await subscription_tier_dao.select_model_by_column(db, tier_name=subscription.tier)
    
    data = SubscriptionInfoResponse(
        user_id=user_id,
        tier=subscription.tier,
        tier_display_name=tier_info.display_name if tier_info else subscription.tier,
        monthly_credits=subscription.monthly_credits,
        current_credits=subscription.current_credits,
        used_credits=subscription.used_credits,
        purchased_credits=subscription.purchased_credits,
        billing_cycle_start=subscription.billing_cycle_start,
        billing_cycle_end=subscription.billing_cycle_end,
        status=subscription.status,
        auto_renew=subscription.auto_renew,
    )
    
    return response_base.success(data=data)


@router.get(
    '/usage',
    summary='获取积分使用统计',
    description='获取当前用户的积分使用统计，使用 x-api-key 认证',
)
async def get_credit_usage(
    db: CurrentSession,
    x_api_key: Annotated[str, Header(alias='x-api-key', description='LLM API Key (sk-cf-xxx)')],
    days: Annotated[int, Query(description='统计天数', ge=1, le=90)] = 30,
) -> ResponseSchemaModel[CreditUsageSummary]:
    """获取积分使用统计"""
    # 从 API Key 获取用户信息
    api_key_obj = await api_key_service.verify_api_key(db, x_api_key)
    user_id = api_key_obj.user_id
    
    # 获取积分使用统计
    from backend.app.user_tier.crud.crud_credit_transaction import credit_transaction_dao
    from sqlalchemy import select, func
    from backend.app.user_tier.model import CreditTransaction
    from backend.utils.timezone import timezone
    from datetime import timedelta
    
    start_date = timezone.now() - timedelta(days=days)
    
    # 总使用积分
    stmt = select(
        func.sum(func.abs(CreditTransaction.credits)).label('total'),
        func.count(CreditTransaction.id).label('count'),
    ).where(
        CreditTransaction.user_id == user_id,
        CreditTransaction.transaction_type == 'usage',
        CreditTransaction.created_at >= start_date,
    )
    result = await db.execute(stmt)
    row = result.first()
    
    total_credits_used = row.total or Decimal('0')
    total_transactions = row.count or 0
    
    # 按模型分组统计 (从 extra_data 中提取)
    # 简化实现，暂时返回空列表
    usage_by_model = []
    
    data = CreditUsageSummary(
        total_credits_used=total_credits_used,
        total_transactions=total_transactions,
        usage_by_model=usage_by_model,
    )
    
    return response_base.success(data=data)


@router.get(
    '/transactions',
    summary='获取积分交易历史',
    description='获取当前用户的积分交易历史记录，使用 x-api-key 认证',
)
async def get_credit_transactions(
    db: CurrentSession,
    x_api_key: Annotated[str, Header(alias='x-api-key', description='LLM API Key (sk-cf-xxx)')],
    page: Annotated[int, Query(description='页码', ge=1)] = 1,
    page_size: Annotated[int, Query(description='每页数量', ge=1, le=100)] = 20,
    transaction_type: Annotated[str | None, Query(description='交易类型')] = None,
) -> ResponseSchemaModel[CreditTransactionListResponse]:
    """获取积分交易历史"""
    # 从 API Key 获取用户信息
    api_key_obj = await api_key_service.verify_api_key(db, x_api_key)
    user_id = api_key_obj.user_id
    
    # 查询交易记录
    from sqlalchemy import select, func, desc
    from backend.app.user_tier.model import CreditTransaction
    
    # 构建查询条件
    conditions = [CreditTransaction.user_id == user_id]
    if transaction_type:
        conditions.append(CreditTransaction.transaction_type == transaction_type)
    
    # 查询总数
    count_stmt = select(func.count(CreditTransaction.id)).where(*conditions)
    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0
    
    # 查询数据
    offset = (page - 1) * page_size
    data_stmt = (
        select(CreditTransaction)
        .where(*conditions)
        .order_by(desc(CreditTransaction.created_at))
        .offset(offset)
        .limit(page_size)
    )
    data_result = await db.execute(data_stmt)
    transactions = data_result.scalars().all()
    
    items = [
        CreditTransactionItem(
            id=t.id,
            transaction_type=t.transaction_type,
            credits=t.credits,
            balance_before=t.balance_before,
            balance_after=t.balance_after,
            reference_type=t.reference_type,
            description=t.description,
            created_at=t.created_at,
        )
        for t in transactions
    ]
    
    data = CreditTransactionListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )
    
    return response_base.success(data=data)

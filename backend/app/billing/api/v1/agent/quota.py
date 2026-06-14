"""Agent 配额查询 API

路径前缀: /api/v1/user_tier/agent/quota
认证方式: Agent JWT (DependsAgentJwtAuth)

供 OpenClaw Agent 查询用户配额、积分余额。

@author Ysf
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel
from decimal import Decimal

from backend.app.billing.service.credit_service import credit_service
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.common.dataclasses import AgentTokenPayload
from backend.database.db import CurrentSession

router = APIRouter()

# ==================== Response Schemas ====================

class QuotaResponse(BaseModel):
    """配额查询响应"""
    user_id: int
    tier: str
    tier_display_name: str | None = None
    status: str
    monthly_credits: Decimal
    current_credits: Decimal
    used_credits: Decimal
    available: bool  # 是否还有可用额度

class DeductRequest(BaseModel):
    """积分扣减请求"""
    user_id: int
    credits: Decimal
    model_name: str | None = None
    description: str | None = None

class DeductResponse(BaseModel):
    """积分扣减响应"""
    success: bool
    remaining_credits: Decimal
    message: str

# ==================== APIs ====================

@router.get(
    '/{user_id}',
    summary='查询用户配额',
    description='查询指定用户的订阅状态和积分余额',
    dependencies=[DependsAgentJwtAuth],
)
async def get_user_quota(
    user_id: int,
    request: Request,
    db: CurrentSession,
) -> ResponseSchemaModel[QuotaResponse]:
    """查询用户配额"""
    app_code = request.state.app_code

    info = await credit_service.get_user_credits_info(db, user_id, app_code)

    available = (
        info['status'] == 'active'
        and float(info['current_credits']) > 0
    )

    data = QuotaResponse(
        user_id=info['user_id'],
        tier=info['tier'],
        tier_display_name=info['tier_display_name'],
        status=info['status'],
        monthly_credits=Decimal(str(info['monthly_credits'])),
        current_credits=Decimal(str(info['current_credits'])),
        used_credits=Decimal(str(info['used_credits'])),
        available=available,
    )

    return response_base.success(data=data)

@router.post(
    '/deduct',
    summary='扣减用户积分',
    description='模型调用计费时扣减用户积分',
    dependencies=[DependsAgentJwtAuth],
)
async def deduct_credits(
    request: Request,
    db: CurrentSession,
    body: DeductRequest,
) -> ResponseSchemaModel[DeductResponse]:
    """扣减积分"""
    app_code = request.state.app_code

    try:
        subscription = await credit_service.deduct_credits(
            db,
            user_id=body.user_id,
            credits=body.credits,
            transaction_type='model_call',
            reference_type='model',
            description=body.description or f'模型调用: {body.model_name or "unknown"}',
            app_code=app_code,
        )

        return response_base.success(data=DeductResponse(
            success=True,
            remaining_credits=subscription.current_credits,
            message='扣减成功',
        ))
    except Exception as e:
        return response_base.fail(data=DeductResponse(
            success=False,
            remaining_credits=Decimal('0'),
            message=str(e),
        ))

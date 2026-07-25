"""Agent 配额查询 API

路径前缀: /api/v1/user_tier/agent/quota
认证方式: Agent JWT (DependsAgentJwtAuth)

供 OpenClaw Agent 查询用户配额、积分余额。

@author Ysf
"""

from decimal import Decimal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.app.billing.service.credit_service import credit_service
from backend.common.response.response_schema import ResponseSchemaModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
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
    # 余额读不到就是 None，不伪造 0——假 0 会让调用方以为额度用光了
    current_credits: Decimal | None = None
    credit_status: str = 'ok'
    used_credits: Decimal
    available: bool  # 是否还有可用额度（仅展示；硬门禁在 NewAPI relay）


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

    # 余额读不到时 current_credits 为 None：那是「不知道」，不是「没有额度」。
    # 这里如实返回 None 并把 available 交给硬门禁——能不能发起调用只有一处判定，
    # 就是 NewAPI relay 的 403。
    current_credits = Decimal(str(info['current_credits'])) if info.get('current_credits') is not None else None
    available = info['status'] == 'active' and (current_credits is None or current_credits > 0)

    data = QuotaResponse(
        user_id=info['user_id'],
        tier=info['tier'],
        tier_display_name=info['tier_display_name'],
        status=info['status'],
        monthly_credits=Decimal(str(info['monthly_credits'])),
        current_credits=current_credits,
        credit_status=info.get('credit_status', 'ok'),
        used_credits=Decimal(str(info['used_credits'])),
        available=available,
    )

    return response_base.success(data=data)

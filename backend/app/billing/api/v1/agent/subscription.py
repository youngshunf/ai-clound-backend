"""Agent 订阅查询 API

路径前缀: /api/v1/user_tier/agent/subscriptions
认证方式: Agent JWT (DependsAgentJwtAuth)

供 Agent 代查用户订阅状态和积分信息。
"""
from typing import Annotated

from fastapi import APIRouter, Query, Request

from backend.app.billing.service.credit_service import credit_service
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.common.security.agent_utils import resolve_user_id
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '',
    summary='查询用户订阅信息（Agent端）',
    description='Agent 通过 user_uuid 查询用户订阅状态和积分余额',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_get_subscription(
    request: Request,
    db: CurrentSession,
    user_uuid: Annotated[str, Query(description='用户 UUID (sys_user.uuid)')],
) -> ResponseModel:
    user_id = await resolve_user_id(db, user_uuid)
    app_code = getattr(request.state, 'app_code', 'huanxing')

    info = await credit_service.get_user_credits_info(db, user_id, app_code)

    return response_base.success(data={
        'user_id': info['user_id'],
        'tier': info['tier'],
        'tier_display_name': info['tier_display_name'],
        'status': info['status'],
        'monthly_credits': info['monthly_credits'],
        'current_credits': info['current_credits'],
        'used_credits': info['used_credits'],
        'billing_cycle_start': info['billing_cycle_start'],
        'billing_cycle_end': info['billing_cycle_end'],
    })

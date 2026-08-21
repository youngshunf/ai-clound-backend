"""Agent 用量统计 API

路径前缀: /api/v1/user_tier/agent/usage
认证方式: Agent JWT (DependsAgentJwtAuth)

供 Agent 代查用户使用统计。
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
    summary='查询用户用量统计（Agent端）',
    description='Agent 通过 user_uuid 查询用户积分使用统计',
    dependencies=[DependsAgentJwtAuth],
)
async def agent_get_usage(
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
        'current_credits': info['current_credits'],
        'used_credits': info['used_credits'],
        'monthly_credits': info['monthly_credits'],
        # 周期剩余读不到时如实为 null（不补 0）：分身据此措辞时，
        # 「不知道还剩多少」和「一点都不剩了」是两句完全不同的话。
        'monthly_remaining': info['monthly_remaining'],
        'purchased_credits': info.get('purchased_credits', 0),
        'bonus_remaining': info['bonus_remaining'],
    })

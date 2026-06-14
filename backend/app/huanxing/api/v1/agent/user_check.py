"""唤星 Agent 用户预检 API

路径前缀: /api/v1/huanxing/agent/users
认证方式: Agent JWT (DependsAgentJwtAuth)

check-phone: 验证码校验通过后，检查该手机号在跨服务器的 Agent 分布情况。
"""
from typing import Annotated

from fastapi import APIRouter, Query

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.admin.model.user import User
from backend.app.huanxing.crud.crud_huanxing_user import huanxing_user_dao
from backend.app.billing.model.user_subscription import UserSubscription
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt_auth import DependsAgentJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


@router.get(
    '/check-phone',
    summary='按手机号查询用户 Agent 分布',
    description='验证码通过后调用，查该手机号在所有服务器的 Agent 情况和订阅限制',
    dependencies=[DependsAgentJwtAuth],
)
async def check_phone(
    db: CurrentSession,
    phone: Annotated[str, Query(description='手机号')],
) -> ResponseModel:
    # 1. 查 sys_user
    stmt = sa.select(User.id, User.uuid).where(User.phone == phone)
    result = await db.execute(stmt)
    user_row = result.first()

    if not user_row:
        return response_base.success(data={
            'exists': False,
            'user_uuid': None,
            'tier': None,
            'max_agents': 1,
            'agent_count': 0,
            'servers': [],
        })

    user_id, user_uuid = user_row

    # 2. 查所有 Agent
    agents = await huanxing_user_dao.get_all_by_user_id(db, user_uuid)
    active_agents = [a for a in agents if a.agent_status == 1]

    # 3. 查订阅（读 max_agents）
    sub_stmt = sa.select(
        UserSubscription.tier,
        UserSubscription.max_agents,
        UserSubscription.status,
    ).where(
        UserSubscription.user_id == user_id,
        UserSubscription.app_code == 'huanxing',
    )
    sub_result = await db.execute(sub_stmt)
    sub_row = sub_result.first()

    tier = sub_row.tier if sub_row else 'free'
    max_agents = sub_row.max_agents if sub_row else 1

    return response_base.success(data={
        'exists': len(active_agents) > 0,
        'user_uuid': user_uuid,
        'tier': tier,
        'max_agents': max_agents,
        'agent_count': len(active_agents),
        'servers': [
            {
                'server_id': a.server_id,
                'agent_id': a.agent_id,
                'agent_status': a.agent_status,
                'template': a.template,
            }
            for a in agents
        ],
    })

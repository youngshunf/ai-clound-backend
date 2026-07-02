"""
Agent JWT 刷新接口

Owner 使用自己的 JWT 为名下的 Agent 刷新 token。
用于 Agent JWT 过期后的续期，无需重新登录。

@author Ysf
@date 2026-05-13
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.common.exception import errors
from backend.common.response.response_schema import ResponseModel, response_base
from backend.common.security.agent_jwt import create_agent_access_token
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import CurrentSession

router = APIRouter()


class RefreshAgentTokenRequest(BaseModel):
    """刷新 Agent Token 请求"""
    agent_hasn_id: str = Field(description='Agent 的 HASN ID')


class RefreshAgentTokenResponse(BaseModel):
    """刷新 Agent Token 响应"""
    access_token: str = Field(description='新的 Agent JWT')
    # scopes 已退役（实施102 S0）：JWT 不再携带 scopes，授权只看三态。恒空占位，兼容旧 daemon。
    scopes: list[str] = Field(default_factory=list, description='已退役占位（恒空），授权走三态 capability_modes')
    expire_time: str = Field(description='过期时间')


@router.post(
    '/agent-token/refresh',
    summary='刷新 Agent JWT',
    description='Owner 使用自己的 JWT 为名下的 Agent 刷新 token',
    dependencies=[DependsJwtAuth],
    response_model=ResponseModel,
)
async def refresh_agent_token(
    request: Request,
    db: CurrentSession,
    body: RefreshAgentTokenRequest,
) -> ResponseModel:
    """
    刷新 Agent JWT

    **认证方式**: Owner JWT (Bearer Token)

    **请求体**:
    ```json
    {
      "agent_hasn_id": "a_xxx"
    }
    ```

    **响应**:
    ```json
    {
      "code": 200,
      "data": {
        "access_token": "eyJhbGc...",
        "scopes": ["community.read", "message.send", ...],
        "expire_time": "2026-05-13T12:00:00Z"
      }
    }
    ```

    **错误码**:
    - 40301: Agent 不属于当前 Owner
    - 40401: Agent 不存在
    """
    owner_user_id = request.user.id
    agent_hasn_id = body.agent_hasn_id

    # 查询 Agent 是否存在且属于当前 Owner
    from sqlalchemy import text
    from loguru import logger

    logger.info(f"刷新 Agent token: agent_hasn_id={agent_hasn_id}, owner_user_id={owner_user_id}")

    result = await db.execute(
        text("""
            SELECT ha.hasn_id, ha.display_name, hh.hasn_id as owner_hasn_id
            FROM hasn_agents ha
            JOIN hasn_humans hh ON ha.owner_id = hh.hasn_id
            WHERE ha.hasn_id = :agent_hasn_id
              AND hh.user_id = :owner_user_id
              AND ha.status = 'active'
        """),
        {
            "agent_hasn_id": agent_hasn_id,
            "owner_user_id": owner_user_id,
        }
    )
    row = result.fetchone()

    if not row:
        # 调试：查询 Agent 是否存在
        debug_result = await db.execute(
            text("SELECT hasn_id, owner_id, status FROM hasn_agents WHERE hasn_id = :agent_hasn_id"),
            {"agent_hasn_id": agent_hasn_id}
        )
        debug_row = debug_result.fetchone()
        if debug_row:
            logger.warning(f"Agent 存在但查询失败: hasn_id={debug_row[0]}, owner_id={debug_row[1]}, status={debug_row[2]}")
            # 查询 owner 信息
            owner_result = await db.execute(
                text("SELECT hasn_id, user_id FROM hasn_humans WHERE hasn_id = :owner_id"),
                {"owner_id": debug_row[1]}
            )
            owner_row = owner_result.fetchone()
            if owner_row:
                logger.warning(f"Owner 信息: hasn_id={owner_row[0]}, user_id={owner_row[1]}, 期望 user_id={owner_user_id}")
            else:
                logger.warning(f"Owner 不存在: owner_id={debug_row[1]}")
        else:
            logger.warning(f"Agent 不存在: agent_hasn_id={agent_hasn_id}")

        raise errors.NotFoundError(msg='Agent 不存在或不属于当前用户')

    agent_display_name = row[1]
    owner_hasn_id = row[2]

    # 签发新的 Agent JWT（scopes 已退役·实施102 S0：JWT 不携带 scopes，授权只看三态）
    agent_token = await create_agent_access_token(
        agent_hasn_id=agent_hasn_id,
        agent_name=agent_display_name,
        owner_hasn_id=owner_hasn_id,
        owner_user_id=owner_user_id,
    )

    return response_base.success(data={
        'access_token': agent_token.access_token,
        # scopes 已退役（实施102 S0）：恒空占位，兼容旧 daemon 反序列化。
        'scopes': [],
        'expire_time': agent_token.access_token_expire_time.isoformat(),
    })

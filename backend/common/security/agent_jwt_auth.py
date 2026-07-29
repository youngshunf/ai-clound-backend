"""
Agent JWT / Agent MCP Key 认证依赖

Agent 通过 Authorization Bearer 传递独立 JWT 或长期可吊销的 ``hasn_amk_*``。
两者都只从已验证凭证自识别 Agent/主人，禁止依赖请求头声明身份。

认证方式: Header `Authorization: Bearer <agent_jwt|agent_mcp_key>`
路由前缀: /api/v1/hasn/agent/

@author Ysf
@date 2026-05-13
"""

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from backend.app.hasn.model import HasnAgents, HasnHumans
from backend.app.hasn.service.hasn_agent_mcp_keys_service import hasn_agent_mcp_keys_service
from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.log import log
from backend.common.security.agent_jwt import verify_agent_token
from backend.database.db import CurrentSession
from backend.utils.timezone import timezone

# Bearer token 提取器
_bearer_scheme = HTTPBearer(auto_error=False)


async def agent_jwt_auth(
    request: Request,
    db: CurrentSession,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AgentTokenPayload:
    """
    Agent JWT 认证依赖

    用法::

        @router.get('/xxx', dependencies=[DependsAgentJwtAuth])
        async def xxx(request: Request):
            agent = request.state.agent
            agent_hasn_id = agent.agent_hasn_id
            owner_hasn_id = agent.owner_hasn_id

    或作为参数注入::

        @router.get('/xxx')
        async def xxx(agent: AgentTokenPayload = DependsAgentJwtAuth):
            agent_hasn_id = agent.agent_hasn_id

    :return: AgentTokenPayload
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail='缺少 Authorization header。请提供 Bearer token。',
        )

    token = credentials.credentials

    try:
        if token.startswith('hasn_amk_'):
            record = await hasn_agent_mcp_keys_service.verify(
                db,
                presented_key=token,
                node_id=request.headers.get('X-Node-Id'),
            )
            agent_row = (
                await db.execute(select(HasnAgents).where(HasnAgents.hasn_id == record.agent_hasn_id).limit(1))
            ).scalar_one_or_none()
            if agent_row is None or agent_row.status != 'active':
                raise errors.AuthorizationError(msg='Agent 不存在或非活跃状态')
            owner_user_id = record.owner_user_id
            if owner_user_id is None:
                owner_user_id = (
                    await db.execute(
                        select(HasnHumans.user_id).where(HasnHumans.hasn_id == record.owner_hasn_id).limit(1)
                    )
                ).scalar_one_or_none()
            if owner_user_id is None:
                raise errors.AuthorizationError(msg='Agent MCP Key 无法解析主人')
            agent_payload = AgentTokenPayload(
                agent_hasn_id=record.agent_hasn_id,
                agent_name=agent_row.agent_name or '',
                owner_hasn_id=record.owner_hasn_id,
                owner_user_id=int(owner_user_id),
                session_uuid=f'amk_{record.id}',
                expire_time=record.expire_time or timezone.now(),
            )
            request.state.agent = agent_payload
            log.info(f'Agent MCP Key 认证成功: {agent_payload.agent_hasn_id}')
            return agent_payload

        # 验证 Agent JWT
        agent_payload = await verify_agent_token(token)

        # 注入到 request.state 方便后续使用
        request.state.agent = agent_payload

        log.info(f'Agent JWT 认证成功: {agent_payload.agent_hasn_id} (owner: {agent_payload.owner_hasn_id})')

        return agent_payload

    except errors.TokenError as e:
        raise HTTPException(
            status_code=401,
            detail=f'Agent JWT 验证失败: {e!s}',
        )
    except Exception as e:
        log.error(f'Agent JWT 认证异常: {e!s}')
        raise HTTPException(
            status_code=401,
            detail='Agent JWT 认证失败',
        )


# NOTE(实施102 S0): `require_scopes` 装饰器与 `check_scopes` 已退役——它们据
# JWT `scopes`（一个对所有 Agent 恒定的死快照）做「静态注册闸」，从不是 per-agent
# 授权。REST Agent 业务面改用 `agent_capability.require_capability_not_denied`
# 的 deny-only 三态闸；工具面授权由三态 capability_modes 权威裁定。


# FastAPI 依赖注入快捷方式
DependsAgentJwtAuth = Depends(agent_jwt_auth)

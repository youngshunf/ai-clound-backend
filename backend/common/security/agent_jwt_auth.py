"""
Agent JWT 认证依赖

Agent 使用独立的 JWT 进行身份认证，通过 Authorization header 传递。
支持细粒度的 Scope 权限控制。

认证方式: Header `Authorization: Bearer <agent_jwt>`
路由前缀: /api/v1/hasn/agent/

@author Ysf
@date 2026-05-13
"""
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.common.dataclasses import AgentTokenPayload
from backend.common.exception import errors
from backend.common.log import log
from backend.common.security.agent_jwt import verify_agent_token
from backend.database.db import CurrentSession

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

        @router.get("/xxx", dependencies=[DependsAgentJwtAuth])
        async def xxx(request: Request):
            agent = request.state.agent
            agent_hasn_id = agent.agent_hasn_id
            owner_hasn_id = agent.owner_hasn_id

    或作为参数注入::

        @router.get("/xxx")
        async def xxx(agent: AgentTokenPayload = DependsAgentJwtAuth):
            agent_hasn_id = agent.agent_hasn_id

    :return: AgentTokenPayload
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="缺少 Authorization header。请提供 Bearer token。",
        )

    token = credentials.credentials

    try:
        # 验证 Agent JWT
        agent_payload = await verify_agent_token(token)

        # 注入到 request.state 方便后续使用
        request.state.agent = agent_payload

        log.info(
            f"Agent JWT 认证成功: {agent_payload.agent_hasn_id} "
            f"(owner: {agent_payload.owner_hasn_id})"
        )

        return agent_payload

    except errors.TokenError as e:
        raise HTTPException(
            status_code=401,
            detail=f"Agent JWT 验证失败: {str(e)}",
        )
    except Exception as e:
        log.error(f"Agent JWT 认证异常: {str(e)}")
        raise HTTPException(
            status_code=401,
            detail="Agent JWT 认证失败",
        )


# NOTE(实施102 S0): `require_scopes` 装饰器与 `check_scopes` 已退役——它们据
# JWT `scopes`（一个对所有 Agent 恒定的死快照）做「静态注册闸」，从不是 per-agent
# 授权。REST Agent 业务面改用 `agent_capability.require_capability_not_denied`
# 的 deny-only 三态闸；工具面授权由三态 capability_modes 权威裁定。


# FastAPI 依赖注入快捷方式
DependsAgentJwtAuth = Depends(agent_jwt_auth)

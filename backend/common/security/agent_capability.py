"""Agent 能力三态 deny 硬闸（REST Agent 业务面纵深防御）。

背景（doc17 / 实施102 S0）：REST Agent 业务面历史上用 ``check_scopes`` 卡门，
比对凭证 JWT claim 里的 ``scopes`` 持有量。但 ``scopes`` 已退役成人人相同的固定
常量（16-doc D-v3-2），``check_scopes`` 因此是「scope 字面量是否被平台注册」的
假闸门，不是 per-agent 授权。判定权威只有三态：
``hasn_agent_scopes.{default_mode, capability_modes}``（消费时活取）。

本 helper 让 REST Agent 写类端点消费三态的 **deny 硬闸**（纵深防御）：

- ``capability_modes[scope] == 'deny'``，或（无显式项且 ``default_mode == 'deny'``）
  → 403 拒绝；
- ``ask`` / ``allow`` / 未配置且 default 非 deny → 放行。

为什么 HTTP 面只消费 deny、不消费 ask：ask 的挂起/审批卡链路归工具面
（doc15 / doc18 G5），HTTP 面没有审批回路。这些端点的正常调用方（云端平台
工具层 / daemon BackendGateway agent 通道）在工具面已过完整三态闸，HTTP 面的
deny 闸是纵深防御，不是第二套审批。
"""

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.security.agent_jwt import get_agent_scopes_cached


async def require_capability_not_denied(db: AsyncSession, agent_hasn_id: str, scope: str) -> None:
    """三态 deny 硬闸：能力被主人 deny 则 403，否则放行。

    :param db: 数据库会话
    :param agent_hasn_id: Agent 的 HASN ID
    :param scope: 能力键（colon-scope，如 ``deck:write``）
    :raises HTTPException: 能力被 deny 时抛 403
    """
    config = await get_agent_scopes_cached(agent_hasn_id, db)
    capability_modes = config.get('capability_modes') or {}
    mode = capability_modes.get(scope)
    denied = mode == 'deny' if mode is not None else config.get('default_mode') == 'deny'
    if denied:
        raise HTTPException(
            status_code=403,
            detail=f'能力已被主人禁用（deny）：{scope}',
        )

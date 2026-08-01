"""App scope 端点的主人身份解析（本模块三个端点共用一份，杜绝各写一遍再各漏一处）。

**为什么必须 fail-closed 成 401，而不是让 `.id` 抛 AttributeError**：认证中间件对 Agent JWT 是
**放行**的（`is_agent_token` 分流，让 agent 面自己校验），因此拿 Agent JWT 打 app scope 端点时
`request.user` 是 `UnauthenticatedUser`——它没有 `.id`，直接取会抛 `AttributeError` 变成 **500**。

scope 越界必须是明确的 401：500 会被 daemon 当成「服务器故障」按可重试处置反复重打，而这条请求
无论重试多少次都不会成功；主人那边看到的则是一个查不出原因的服务器错误，而不是「你用错了凭据」。

⚠️ 本模块三处（`owner_memory` / `owner_profile_coverage` / `merge_status`）历史上各自复制过一份
同款解析，其中两处漏了这道守卫。新增 app scope 端点一律 `from .owner_scope import resolve_owner_id`，
不要再复制。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from backend.app.hasn_core import HasnHumans
from backend.common.exception import errors

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession


async def resolve_owner_id(request: Request, db: AsyncSession) -> str:
    """由当前登录用户解析 owner 的 `hasn_id`；非 Owner JWT 一律 401。"""
    user_id = getattr(getattr(request, 'user', None), 'id', None)
    if user_id is None:
        raise errors.TokenError(msg='本端点需要主人身份（Owner JWT）')
    owner = (
        await db.execute(sa.select(HasnHumans.hasn_id).where(HasnHumans.user_id == user_id).limit(1))
    ).scalar_one_or_none()
    if not owner:
        raise errors.ForbiddenError(msg='当前用户未注册 HASN 身份')
    return owner

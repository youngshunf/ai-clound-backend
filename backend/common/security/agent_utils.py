"""Agent 认证通用工具

提供 Agent API 中常用的辅助函数。
"""
import sqlalchemy as sa

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.admin.model.user import User


async def resolve_user_id(db: AsyncSession, user_uuid: str) -> int:
    """从 sys_user.uuid 解析出 sys_user.id

    Agent 端传的是 uuid 字符串，但业务表（文档、订阅等）存的是 int user_id。
    此函数做统一转换。

    :param db: 数据库会话
    :param user_uuid: 用户 UUID (sys_user.uuid)
    :return: sys_user.id (int)
    :raises HTTPException: 用户不存在时返回 404
    """
    stmt = sa.select(User.id).where(User.uuid == user_uuid)
    result = await db.execute(stmt)
    user_id = result.scalar_one_or_none()
    if user_id is None:
        raise HTTPException(status_code=404, detail=f'用户不存在: {user_uuid}')
    return user_id

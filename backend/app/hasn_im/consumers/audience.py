"""集成事件受众解析。

消息事实按投影时刻读取当前名册；成员变更事实必须优先使用写事务冻结的变更前后身份集合，
确保被移除成员的主人也能收到失效事件并清理本地镜像。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.service import conversation_projection as cp


async def resolve_audience_owner_ids(
    db: AsyncSession,
    *,
    conversation_id: str,
    frozen_hasn_ids: tuple[str, ...] = (),
) -> list[str]:
    """解析 owner 受众；冻结身份存在时不回退到已变化的当前名册。"""
    if frozen_hasn_ids:
        resolved = await cp._resolve_owner_ids(
            db,
            list(dict.fromkeys(frozen_hasn_ids)),
        )
        return sorted(
            {owner_id for owner_id in resolved.values() if owner_id}
        )

    conversation = await cp._fetch_conversation(db, conversation_id)
    if conversation is None:
        return []
    members = (
        await cp._load_group_members(db, str(conversation.id))
        if conversation.type == 'group'
        else None
    )
    return await cp.compute_audience_owner_ids(
        db,
        conversation,
        members=members,
    )

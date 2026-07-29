"""集成事件受众解析。

消息事实按消息序号读取事件发生时的成员周期，避免成员在异步投影前退出后永久漏掉消息或
撤回；成员变更事实优先使用写事务冻结的变更前后身份集合。
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnConversationMemberships
from backend.app.hasn.service import conversation_projection as cp


async def resolve_audience_owner_ids(
    db: AsyncSession,
    *,
    conversation_id: str,
    frozen_hasn_ids: tuple[str, ...] = (),
    conversation_seq: int | None = None,
) -> list[str]:
    """解析 owner 受众；优先采用冻结身份或事件发生时的成员周期。"""
    if frozen_hasn_ids:
        resolved = await cp._resolve_owner_ids(
            db,
            list(dict.fromkeys(frozen_hasn_ids)),
        )
        return sorted({owner_id for owner_id in resolved.values() if owner_id})

    conversation = await cp._fetch_conversation(db, conversation_id)
    if conversation is None:
        return []
    members = None
    if conversation.type == 'group':
        if conversation_seq is None:
            members = await cp._load_group_members(db, str(conversation.id))
        else:
            members = list(
                (
                    await db.execute(
                        select(HasnConversationMemberships).where(
                            HasnConversationMemberships.conversation_id == conversation.id,
                            HasnConversationMemberships.joined_seq <= conversation_seq,
                            or_(
                                HasnConversationMemberships.left_seq.is_(None),
                                HasnConversationMemberships.left_seq >= conversation_seq,
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
    return await cp.compute_audience_owner_ids(
        db,
        conversation,
        members=members,
    )

"""hasn_im.application.membership_service · 成员周期 epoch 操作 + 未读投影 reconciler（R2-03）

16 号 §4.2/§4.3：成员多周期 epoch（退出闭合不删行、重入建新行、direct 双方永久 epoch），
已读游标 `read_seq` 单调 clamp 到可见上界，未读数由权威序号重算（不退回读改写计数）。

本模块承载对 `hasn_conversation_memberships` / `hasn_unread_projection` 的**单事务 use case**
（不 commit，由调用方与业务写同事务提交）；纯不变量逻辑复用 [domain.membership]。
"""

from __future__ import annotations

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model import HasnConversationMemberships, HasnMessages, HasnUnreadProjection
from backend.app.hasn_im.domain import membership as membership_domain
from backend.utils.timezone import timezone

# 撤回消息不计未读（§4.3「message 对该主体可见」的落库近似：status=4 为已撤回）
_RECALLED_STATUS = 4


async def get_active_epoch(
    db: AsyncSession, conversation_id: str, member_hasn_id: str
) -> HasnConversationMemberships | None:
    """取某成员在会话内的活动周期（left_seq IS NULL），无则 None。"""
    result = await db.execute(
        select(HasnConversationMemberships).where(
            HasnConversationMemberships.conversation_id == conversation_id,
            HasnConversationMemberships.member_hasn_id == member_hasn_id,
            HasnConversationMemberships.left_seq.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def join_epoch(
    db: AsyncSession,
    conversation_id: str,
    member_hasn_id: str,
    *,
    current_seq: int,
    member_type: str = 'human',
    role: str = 'member',
    permanent: bool = False,
    joined_seq: int | None = None,
) -> HasnConversationMemberships:
    """创建一个活动周期（首次加入 / 重入同一入口·§4.2）。

    - `joined_seq` 缺省 = `current_seq + 1`（本周期只见加入点之后的消息）；`read_seq = joined_seq - 1`
      （尚未读到本周期任何消息）；
    - `permanent=True`（direct 双方）语义上永不闭合 left_seq，行为上仍是普通活动周期，
      靠调用方不对其 leave 来保证；
    - 部分唯一索引 `uq_hasn_membership_active_epoch` 保证同一 (会话, 成员) 只有一个活动周期，
      已有活动周期时重复 join 会撞唯一约束（调用方须先 leave 或复用现有周期）。
    """
    effective_joined = joined_seq if joined_seq is not None else current_seq + 1
    row = HasnConversationMemberships(
        conversation_id=conversation_id,
        member_hasn_id=member_hasn_id,
        member_type=member_type,
        role=role,
        joined_seq=effective_joined,
        left_seq=None,
        read_seq=membership_domain.rejoin_read_seq(effective_joined),
        state='active',
        joined_at=timezone.now(),
    )
    db.add(row)
    await db.flush()
    return row


async def leave_epoch(
    db: AsyncSession,
    conversation_id: str,
    member_hasn_id: str,
    *,
    current_seq: int,
    state: str = 'left',
) -> HasnConversationMemberships | None:
    """闭合活动周期（退出/被移除/被封·§4.2）：写 `left_seq=current_seq` + state，不删行。

    只影响 left_seq IS NULL 的活动周期；无活动周期返回 None（幂等）。
    """
    epoch = await get_active_epoch(db, conversation_id, member_hasn_id)
    if epoch is None:
        return None
    epoch.left_seq = current_seq
    epoch.state = state
    epoch.left_at = timezone.now()
    await db.flush()
    return epoch


async def rejoin_epoch(
    db: AsyncSession,
    conversation_id: str,
    member_hasn_id: str,
    *,
    current_seq: int,
    member_type: str = 'human',
    role: str = 'member',
) -> HasnConversationMemberships:
    """重新加入：创建**新** membership 行（新周期），不覆盖旧周期（§4.2 D8）。

    等价于对同一入口再 join——新行 joined_seq=current_seq+1、read_seq=joined_seq-1；旧闭合周期
    的可见区间保持不变。调用方须保证此前该成员已 leave（无活动周期），否则撞唯一约束。
    """
    return await join_epoch(
        db,
        conversation_id,
        member_hasn_id,
        current_seq=current_seq,
        member_type=member_type,
        role=role,
    )


async def advance_read_seq(
    db: AsyncSession,
    conversation_id: str,
    member_hasn_id: str,
    *,
    incoming_seq: int,
    current_seq: int,
) -> int | None:
    """推进活动周期已读游标（§4.3）：`read_seq = max(old, min(incoming, visible_upper_bound))`。

    单调只进、clamp 到本周期可见上界；无活动周期返回 None。返回推进后的 read_seq。
    """
    epoch = await get_active_epoch(db, conversation_id, member_hasn_id)
    if epoch is None:
        return None
    upper = membership_domain.epoch_visible_upper_bound(epoch.left_seq, current_seq)
    new_read = membership_domain.advance_read_seq(epoch.read_seq, incoming_seq, upper)
    epoch.read_seq = new_read
    await db.flush()
    return new_read


async def compute_unread(
    db: AsyncSession, conversation_id: str, member_hasn_id: str
) -> int:
    """权威未读计数（§4.3 谓词）——只从 message.conversation_seq + membership 序号重算：

    ``conversation_seq > read_seq AND 位于活动周期可见区间 AND 未撤回 AND sender != 本成员``

    无活动周期 → 0。sender 归属以「发送方不是本成员 hasn_id」近似『不属于该 viewer owner』
    （单聊/群聊均适用：不计自己发的消息）。
    """
    epoch = await get_active_epoch(db, conversation_id, member_hasn_id)
    if epoch is None:
        return 0
    conditions = [
        HasnMessages.conversation_id == conversation_id,
        HasnMessages.conversation_seq > epoch.read_seq,
        HasnMessages.conversation_seq >= epoch.joined_seq,
        HasnMessages.status != _RECALLED_STATUS,
        HasnMessages.from_id != member_hasn_id,
    ]
    if epoch.left_seq is not None:
        conditions.append(HasnMessages.conversation_seq <= epoch.left_seq)
    result = await db.execute(select(func.count()).select_from(HasnMessages).where(and_(*conditions)))
    return int(result.scalar_one() or 0)


async def rebuild_unread_projection(
    db: AsyncSession,
    conversation_id: str,
    member_hasn_id: str,
    *,
    current_seq: int,
) -> int:
    """未读投影 reconciler（§4.3）：按权威序号重算 → UPSERT `hasn_unread_projection`。

    投影是可重建 read model，永远以此重算结果为准（不做并发读改写）。返回重算后的未读数。
    """
    unread = await compute_unread(db, conversation_id, member_hasn_id)
    existing = (
        await db.execute(
            select(HasnUnreadProjection).where(
                HasnUnreadProjection.conversation_id == conversation_id,
                HasnUnreadProjection.member_hasn_id == member_hasn_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            HasnUnreadProjection(
                conversation_id=conversation_id,
                member_hasn_id=member_hasn_id,
                unread_count=unread,
                computed_at_seq=current_seq,
            )
        )
    else:
        existing.unread_count = unread
        existing.computed_at_seq = current_seq
    await db.flush()
    return unread


async def list_visible_message_seqs(
    db: AsyncSession, conversation_id: str, member_hasn_id: str
) -> list[int]:
    """列出某成员**所有周期**可见的消息序号（跨闭合+活动周期的并集·§4.2）。

    用于「两段可见区间」验收：退出→重入产生两段区间，中间段（离开期间）的消息不可见。
    """
    epochs = (
        await db.execute(
            select(HasnConversationMemberships).where(
                HasnConversationMemberships.conversation_id == conversation_id,
                HasnConversationMemberships.member_hasn_id == member_hasn_id,
            )
        )
    ).scalars().all()
    if not epochs:
        return []
    epoch_clauses = [
        and_(
            HasnMessages.conversation_seq >= e.joined_seq,
            HasnMessages.conversation_seq <= e.left_seq if e.left_seq is not None else True,
        )
        for e in epochs
    ]
    rows = (
        await db.execute(
            select(HasnMessages.conversation_seq)
            .where(HasnMessages.conversation_id == conversation_id, or_(*epoch_clauses))
            .order_by(HasnMessages.conversation_seq)
        )
    ).scalars().all()
    return [int(s) for s in rows]

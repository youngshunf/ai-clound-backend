from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, UniversalText, id_key


class HasnConversationMemberships(Base):
    """HASN 会话成员周期表（多周期 epoch·doc16 §4.2/§4.3）

    一行 = 一次加入周期，而非「某人永远只有一行」：退出闭合 `left_seq` 不删行、重入建新行；
    部分唯一索引 `uq_hasn_membership_active_epoch` 限同一 (会话, 成员) 最多一个活动周期
    （left_seq IS NULL）；direct 双方永久 epoch。权威 = seq + 可见区间 + read_seq。
    """

    __tablename__ = 'hasn_conversation_memberships'

    id: Mapped[id_key] = mapped_column(init=False)
    conversation_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment='所属会话 ID')
    member_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='成员 hasn_id')
    member_type: Mapped[str] = mapped_column(sa.String(10), default='human', comment='成员类型 (human:人类/agent:代理/service:系统)')
    role: Mapped[str] = mapped_column(sa.String(20), default='member', comment='角色 (owner:群主/admin:管理员/member:成员)')
    joined_seq: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='本周期加入时的会话序号下界（可见 message.seq >= joined_seq）')
    left_seq: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='本周期退出时的会话序号上界（NULL=活动周期·可见 message.seq <= left_seq）')
    read_seq: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='本周期已读游标（单调只进·clamp 到可见上界·§4.3）')
    state: Mapped[str] = mapped_column(sa.String(10), default='active', comment='状态 (active:活动/left:主动退出/removed:被移除/banned:被封)')
    agent_group_trust_level: Mapped[int] = mapped_column(sa.SmallInteger(), default=2, comment='分身群内披露档 (2:普通朋友/3:好友/4:密友)·doc08 §3.4')
    agent_charter: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='分身群内发言准则（仅分身主人可读写）')
    joined_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='加入时间')
    left_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='退出时间')

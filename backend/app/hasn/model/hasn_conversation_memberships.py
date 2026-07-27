from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column, synonym

from backend.common.model import Base, TimeZone, UniversalText, id_key
from backend.database.schema_names import IM_SCHEMA


class HasnConversationMemberships(Base):
    """HASN 会话成员周期表（多周期 epoch·doc16 §4.2/§4.3）

    一行 = 一次加入周期，而非「某人永远只有一行」：退出闭合 `left_seq` 不删行、重入建新行；
    部分唯一索引 `uq_hasn_membership_active_epoch` 限同一 (会话, 成员) 最多一个活动周期
    （left_seq IS NULL）；direct 双方永久 epoch。权威 = seq + 可见区间 + read_seq。
    """

    __tablename__ = 'hasn_conversation_memberships'
    __table_args__ = {'schema': IM_SCHEMA}

    id: Mapped[id_key] = mapped_column(init=False)
    conversation_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment='所属会话 ID')
    member_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='成员 hasn_id')
    # 兼容现网群名册展示字段；成员身份权威仍是 member_hasn_id + epoch。
    member_star_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='成员唤星号展示快照')
    member_name: Mapped[str] = mapped_column(sa.String(100), default='', comment='成员名称展示快照')
    member_type: Mapped[str] = mapped_column(sa.String(10), default='human', comment='成员类型 (human:人类/agent:代理/service:系统)')
    role: Mapped[str] = mapped_column(sa.String(20), default='member', comment='角色 (owner:群主/admin:管理员/member:成员)')
    joined_seq: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='本周期加入时的会话序号下界（可见 message.seq >= joined_seq）')
    left_seq: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='本周期退出时的会话序号上界（NULL=活动周期·可见 message.seq <= left_seq）')
    read_seq: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='本周期已读游标（单调只进·clamp 到可见上界·§4.3）')
    state: Mapped[str] = mapped_column(sa.String(10), default='active', comment='状态 (active:活动/left:主动退出/removed:被移除/banned:被封)')
    agent_group_trust_level: Mapped[int] = mapped_column(sa.SmallInteger(), default=2, comment='分身群内披露档 (2:普通朋友/3:好友/4:密友)·doc08 §3.4')
    agent_charter: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='分身群内发言准则（仅分身主人可读写）')
    muted: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=False, comment='是否免打扰')
    invited_by: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='邀请者 hasn_id')
    charter_updated_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='发言准则最后更新时间')
    joined_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='加入时间')
    left_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='退出时间')

    # 收编期只保留 Python 属性别名，物理列始终是 member_hasn_id。
    member_id = synonym('member_hasn_id')

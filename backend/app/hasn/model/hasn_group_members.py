from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, UniversalText, id_key


class HasnGroupMembers(Base):
    """HASN 群成员表"""

    __tablename__ = 'hasn_group_members'

    id: Mapped[id_key] = mapped_column(init=False)
    conversation_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment='群会话 ID（关联 hasn_conversations）')
    member_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='成员 hasn_id')
    member_type: Mapped[str] = mapped_column(sa.String(10), default='', comment='成员类型 (human:人类:blue/agent:代理:green)')
    member_star_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='成员唤星号')
    member_name: Mapped[str] = mapped_column(sa.String(100), default='', comment='成员名称')
    role: Mapped[str] = mapped_column(sa.String(20), default='', comment='角色 (owner:群主:red/admin:管理员:orange/member:成员:blue)')
    muted: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否免打扰')
    joined_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='加入时间')
    invited_by: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='邀请者 hasn_id')
    # doc10 需求 4：分身群内发言准则（仅 member_type=agent 行有意义，由分身主人设置，仅本群生效）
    agent_charter: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='分身群内发言准则（仅分身主人可读写，随派发注入 runtime system_prompt）')
    charter_updated_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='发言准则最后更新时间')

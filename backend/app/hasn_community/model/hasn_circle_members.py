from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class HasnCircleMembers(Base):
    """圈子成员与角色表"""

    __tablename__ = 'hasn_circle_members'

    id: Mapped[id_key] = mapped_column(init=False)
    circle_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='所属圈子 circle_id')
    member_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='成员 hasn_id（Human 或 Agent）')
    member_type: Mapped[str] = mapped_column(sa.String(10), default='', comment='成员类型 (human:人类/agent:分身)')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='成员为 agent 时其主人 hasn_id；human 时=自身')
    role: Mapped[str] = mapped_column(sa.String(20), default='', comment='角色 (owner:圈主:purple/admin:管理员:blue/member:成员:gray)')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (active:正常:green/pending:待审批:orange/banned:已封禁:red/left:已退出:gray)')
    invited_by_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='邀请人 hasn_id（invite 流程）')
    joined_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='加入时间')

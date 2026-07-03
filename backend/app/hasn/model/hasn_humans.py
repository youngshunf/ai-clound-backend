import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, UniversalText, id_key


class HasnHumans(Base):
    """HASN 人类用户身份表"""

    __tablename__ = 'hasn_humans'

    id: Mapped[id_key] = mapped_column(init=False)
    hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='HASN 唯一标识 (h_{uuid})')
    star_id: Mapped[str] = mapped_column(sa.String(30), default='', comment='唤星号 (数字号或自定义号)')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='关联唤星平台用户 ID')
    nickname: Mapped[str] = mapped_column(sa.String(50), default='', comment='昵称（与 sys_user.nickname 对齐）')
    bio: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='个人简介')
    avatar: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='头像（与 sys_user.avatar 对齐）')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (active:正常:green/suspended:已暂停:orange/deleted:已注销:red)')
    contact_policy: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='联系人策略 (JSONB)')
    timezone: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='时区')
    tags: Mapped[list | None] = mapped_column(postgresql.ARRAY(sa.Text()), default=None, comment='个人标签')
    stats: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='统计信息 (JSONB)')
    community_settings: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='社区个人设置 (JSONB)：公开边界/默认评论策略/通知开关')

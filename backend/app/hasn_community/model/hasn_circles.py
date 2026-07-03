import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_community.model._base import CommunityBase
from backend.common.model import UniversalText, id_key


class HasnCircles(CommunityBase):
    """社区圈子实体表"""

    __tablename__ = 'hasn_circles'

    id: Mapped[id_key] = mapped_column(init=False)
    circle_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='全局唯一 ID，格式 cir_{nanoid}')
    name: Mapped[str] = mapped_column(sa.String(80), default='', comment='圈子名称')
    slug: Mapped[str] = mapped_column(sa.String(80), default='', comment='公开路由 /community/circles/{slug}')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='圈子简介')
    cover_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='封面图 URL')
    avatar_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='头像 URL')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='圈主 hasn_id（责任主体，必须为 Human，Agent 不可单独当圈主）')
    origin_workspace_kind: Mapped[str] = mapped_column(sa.String(16), default='', comment='来源 workspace 类型 (personal:个人/enterprise:企业)')
    origin_workspace_id: Mapped[str] = mapped_column(sa.String(80), default='', comment='来源 workspace 标识')
    visibility: Mapped[str] = mapped_column(sa.String(20), default='', comment='可见性 (public:公开圈:green/private:私密圈:gray)')
    join_policy: Mapped[str] = mapped_column(sa.String(20), default='', comment='加入策略 (open:直接加入:green/approval:申请审批:orange/invite:仅邀请:blue)')
    post_policy: Mapped[str] = mapped_column(sa.String(20), default='', comment='发帖策略 (members:成员可发:green/approval:发帖需审:orange/owner_admin:仅管理者:red)')
    member_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='成员数（冗余，异步维护）')
    content_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='内容数（冗余，异步维护）')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (active:正常:green/archived:已归档:gray/blocked:已封禁:red)')

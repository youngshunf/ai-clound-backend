from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText, TimeZone
from backend.app.hasn_community.model._base import CommunityBase
from backend.utils.timezone import timezone


class HasnComments(CommunityBase):
    """社区评论表"""

    __tablename__ = 'hasn_comments'

    id: Mapped[id_key] = mapped_column(init=False)
    comment_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    target_type: Mapped[str] = mapped_column(sa.String(10), default='', comment='post 或 article')
    target_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='帖子的 post_id 或文章的 article_id')
    parent_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='父评论 comment_id（楼中楼回复）')
    root_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='根评论 comment_id（方便查询整个评论线程）')
    author_type: Mapped[str] = mapped_column(sa.String(10), default='', comment=None)
    author_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    author_user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    origin_workspace_kind: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    origin_workspace_id: Mapped[str] = mapped_column(sa.String(80), default='', comment=None)
    content: Mapped[str] = mapped_column(UniversalText, default='', comment=None)
    is_auto_reply: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='Agent 自动回复标识，前端据此展示"自动回复"标签')
    like_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='visible / hidden / deleted')
    created_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)

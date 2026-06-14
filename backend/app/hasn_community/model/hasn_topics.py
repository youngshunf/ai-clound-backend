from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText, TimeZone
from backend.app.hasn_community.model._base import CommunityBase
from backend.utils.timezone import timezone


class HasnTopics(CommunityBase):
    """社区话题实体表"""

    __tablename__ = 'hasn_topics'

    id: Mapped[id_key] = mapped_column(init=False)
    topic_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='全局唯一 ID，格式 tpc_{nanoid}')
    name: Mapped[str] = mapped_column(sa.String(80), default='', comment='展示名（可改）')
    slug: Mapped[str] = mapped_column(sa.String(80), default='', comment='URL 友好标识，公开路由 /community/topics/{slug}，改名不改 slug')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='话题描述')
    cover_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='封面图 URL')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (active:正常:green/merged:已合并:gray/archived:已归档:orange/blocked:已封禁:red)')
    merged_into_topic_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='status=merged 时指向合并目标 topic_id')
    is_featured: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='运营置顶/推荐')
    is_official: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='官方话题标识')
    created_by_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='创建者 hasn_id（用户自建或运营建，可空=系统归一生成）')
    content_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='关联内容数（冗余，异步维护）')
    follow_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='关注数（冗余，异步维护）')
    view_count: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='浏览数（冗余，异步维护）')
    last_active_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最近活跃时间')

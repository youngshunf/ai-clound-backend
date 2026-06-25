from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_creator.model._base import HasnCreatorAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class Publish(HasnCreatorAppBase):
    """发布记录（= content × account：发到某平台账号 + 数据指标）"""

    __tablename__ = 'publish'

    id: Mapped[id_key] = mapped_column(init=False)
    content_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    account_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    platform: Mapped[str] = mapped_column(sa.String(50), default='', comment=None)
    method: Mapped[str] = mapped_column(sa.String(20), default='', comment='方式 (manual_assist:人工辅助:gray/api_auto:API自动:green)')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (draft:草稿:gray/pending_review:待审:orange/approved:已通过:blue/publishing:发布中:cyan/published:已发布:green/failed:失败:red)')
    publish_url: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='发布链接（主人回填 / 系统回填）')
    publish_note: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='给主人看：发布建议（最佳时间/话题标签/置顶评论）')
    approval_user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    approved_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    error_message: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='失败如实回报（零 fake）')
    published_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    views: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    likes: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    comments: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    shares: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    favorites: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    new_followers: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    metrics_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    metrics_updated_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)

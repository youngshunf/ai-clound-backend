from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText, TimeZone
from backend.app.hasn_creator.model._base import HasnCreatorAppBase
from backend.utils.timezone import timezone


class Account(HasnCreatorAppBase):
    """平台账号（1:N project）；同一项目多平台真实账号"""

    __tablename__ = 'account'

    id: Mapped[id_key] = mapped_column(init=False)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    platform: Mapped[str] = mapped_column(sa.String(50), default='', comment='平台 (xiaohongshu:小红书:red/douyin:抖音:gray/wechat_mp:公众号:green/weibo:微博:orange/bilibili:B站:cyan/zhihu:知乎:blue)')
    platform_uid: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment=None)
    nickname: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment=None)
    avatar_url: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    bio: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    home_url: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    followers: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    following: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    total_likes: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    total_favorites: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    total_comments: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    total_posts: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    metrics_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    metrics_updated_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    auth_status: Mapped[str] = mapped_column(sa.String(20), default='', comment='发布授权 (not_configured:未配置:gray/active:已授权:green/expired:已过期:red)')
    is_primary: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment=None)
    notes: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)

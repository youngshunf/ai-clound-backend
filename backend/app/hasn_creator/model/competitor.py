from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_creator.model._base import HasnCreatorAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class Competitor(HasnCreatorAppBase):
    """竞品账号（定位/选题调研输入）"""

    __tablename__ = 'competitor'

    id: Mapped[id_key] = mapped_column(init=False)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    name: Mapped[str] = mapped_column(sa.String(100), default='', comment=None)
    platform: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='平台 (xiaohongshu:小红书:red/douyin:抖音:gray/wechat_mp:公众号:green/weibo:微博:orange/bilibili:B站:cyan/zhihu:知乎:blue)')
    url: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    follower_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    works_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='作品数（分身调研回填；工具层 researched=true 时必填）')
    avg_likes: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    content_style: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    strengths: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    notes: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    tags: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    last_analyzed: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)

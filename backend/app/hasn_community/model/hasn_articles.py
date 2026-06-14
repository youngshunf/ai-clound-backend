from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText, TimeZone
from backend.app.hasn_community.model._base import CommunityBase
from backend.utils.timezone import timezone


class HasnArticles(CommunityBase):
    """社区文章表"""

    __tablename__ = 'hasn_articles'

    id: Mapped[id_key] = mapped_column(init=False)
    article_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    author_type: Mapped[str] = mapped_column(sa.String(10), default='', comment=None)
    author_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    author_user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    co_author_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment=None)
    origin_workspace_kind: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    origin_workspace_id: Mapped[str] = mapped_column(sa.String(80), default='', comment=None)
    title: Mapped[str] = mapped_column(sa.String(200), default='', comment=None)
    summary: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    cover_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment=None)
    content: Mapped[str] = mapped_column(UniversalText, default='', comment=None)
    media_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    reference_cards: Mapped[list] = mapped_column(postgresql.JSONB(), default_factory=list, comment='引用卡片数组 [{type,id,uri,title,summary,access,metadata}]')
    tags: Mapped[list[str]] = mapped_column(postgresql.ARRAY(sa.String), default_factory=list, comment=None)
    skill_tags: Mapped[list[str]] = mapped_column(postgresql.ARRAY(sa.String), default_factory=list, comment=None)
    visibility: Mapped[str] = mapped_column(sa.String(20), default='', comment=None)
    comment_policy: Mapped[str] = mapped_column(sa.String(20), default='', comment=None)
    generation_type: Mapped[str] = mapped_column(sa.String(20), default='', comment=None)
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment=None)
    like_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    comment_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    collect_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    share_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    word_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    read_time_min: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    circle_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='所属圈子 circle_id（NULL=主社区/公共流，非空=只进圈子流）')
    created_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)
    updated_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    published_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)

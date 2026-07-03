from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_community.model._base import CommunityBase
from backend.common.model import TimeZone, UniversalText, id_key
from backend.utils.timezone import timezone


class HasnPosts(CommunityBase):
    """社区帖子表"""

    __tablename__ = 'hasn_posts'

    id: Mapped[id_key] = mapped_column(init=False)
    post_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='全局唯一 ID，格式 p_{nanoid}')
    author_type: Mapped[str] = mapped_column(sa.String(10), default='', comment='human 或 agent')
    author_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='作者的 HASN 身份标识')
    author_user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='关联 sys_user.id，Human 时必填，Agent 时为 NULL')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='责任主体。Human 发帖时 = author_hasn_id；Agent 发帖时 = 主人的 hasn_id')
    co_author_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment=None)
    origin_workspace_kind: Mapped[str] = mapped_column(sa.String(16), default='', comment='内容来源 workspace 类型：personal 或 enterprise')
    origin_workspace_id: Mapped[str] = mapped_column(sa.String(80), default='', comment='来源 workspace 标识：personal 时为 user_id，enterprise 时为 enterprise_id')
    content: Mapped[str] = mapped_column(UniversalText, default='', comment=None)
    media_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    reference_cards: Mapped[list] = mapped_column(postgresql.JSONB(), default_factory=list, comment='引用卡片数组 [{type,id,uri,title,summary,access,metadata}]')
    tags: Mapped[list[str]] = mapped_column(postgresql.ARRAY(sa.String), default_factory=list, comment=None)
    skill_tags: Mapped[list[str]] = mapped_column(postgresql.ARRAY(sa.String), default_factory=list, comment=None)
    visibility: Mapped[str] = mapped_column(sa.String(20), default='', comment='public / followers / private / circle')
    comment_policy: Mapped[str] = mapped_column(sa.String(20), default='', comment='all / followers / closed')
    generation_type: Mapped[str] = mapped_column(sa.String(20), default='', comment='human / agent / co_creation / agent_confirmed')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='draft / pending_review / published / hidden / deleted')
    like_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    comment_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    collect_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    share_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    circle_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='所属圈子 circle_id（NULL=主社区/公共流，非空=只进圈子流）')
    created_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)
    updated_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    published_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)

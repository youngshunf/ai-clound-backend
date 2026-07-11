from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_creator.model._base import HasnCreatorAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class Work(HasnCreatorAppBase):
    """作品明细：自己账号(own)/竞品(competitor)的单条作品 + 指标，按 source_type + *_id 区分归属"""

    __tablename__ = 'work'

    id: Mapped[id_key] = mapped_column(init=False)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    source_type: Mapped[str] = mapped_column(sa.String(16), default='own', comment='来源 (own:自己账号:blue/competitor:竞品:orange)')
    # 自己账号 ID（own 时指向 account.id）/ 竞品 ID（competitor 时指向 competitor.id）
    account_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    competitor_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    platform: Mapped[str] = mapped_column(sa.String(50), default='', comment=None)
    # 归并键：external_id（平台侧作品 ID）与 url（作品原链接，与 publish.published_url 关联）
    external_id: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment=None)
    title: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    url: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    cover_uri: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    published_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    views: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    likes: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    comments: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    shares: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    favorites: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    # 抓取采集时刻（数据新鲜度「更新于 T」诚实标注）
    collected_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)

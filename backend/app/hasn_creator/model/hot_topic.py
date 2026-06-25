from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_creator.model._base import HasnCreatorAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class HotTopic(HasnCreatorAppBase):
    """热榜快照（全局，去重，喂选题；可选数据源）"""

    __tablename__ = 'hot_topic'

    id: Mapped[id_key] = mapped_column(init=False)
    platform_id: Mapped[str] = mapped_column(sa.String(50), default='', comment=None)
    platform_name: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment=None)
    title: Mapped[str] = mapped_column(sa.String(300), default='', comment=None)
    url: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    rank: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    heat_score: Mapped[float] = mapped_column(sa.REAL(), default=0.0, comment=None)
    fetch_source: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    fetched_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    batch_date: Mapped[str] = mapped_column(sa.String(20), default='', comment='批次（去重键：platform_id+url+batch_date）')

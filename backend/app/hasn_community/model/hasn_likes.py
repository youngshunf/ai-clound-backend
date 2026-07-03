from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_community.model._base import CommunityBase
from backend.common.model import TimeZone, id_key
from backend.utils.timezone import timezone


class HasnLikes(CommunityBase):
    """社区点赞表"""

    __tablename__ = 'hasn_likes'

    id: Mapped[id_key] = mapped_column(init=False)
    user_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    target_type: Mapped[str] = mapped_column(sa.String(10), default='', comment=None)
    target_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    created_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)

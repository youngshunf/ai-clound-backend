from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, TimeZone
from backend.app.hasn_community.model._base import CommunityBase
from backend.utils.timezone import timezone


class HasnCollections(CommunityBase):
    """社区收藏夹表"""

    __tablename__ = 'hasn_collections'

    id: Mapped[id_key] = mapped_column(init=False)
    collection_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    name: Mapped[str] = mapped_column(sa.String(100), default='', comment=None)
    is_public: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment=None)
    item_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    created_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment=None)
    updated_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)

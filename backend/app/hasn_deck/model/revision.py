from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_deck.model._base import DeckBase
from backend.common.model import TimeZone, id_key


class Revision(DeckBase):
    """演示文稿版本快照（云端权威历史）"""

    __tablename__ = 'revision'

    id: Mapped[id_key] = mapped_column(init=False)
    deck_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属 deck（引用 deck.deck.id）')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='owner 隔离键')
    seq: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='版本序（单调递增；(deck_id, seq) 唯一）')
    label: Mapped[str] = mapped_column(sa.String(32), default='', comment='来源标签（见 DDL 注释枚举）')
    snapshot: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='deck+pages 快照（JSON）')
    deleted_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='软删时间（非空=已删）')

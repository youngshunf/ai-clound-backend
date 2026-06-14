from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_deck.model._base import DeckBase
from backend.common.model import TimeZone, UniversalText, id_key


class Page(DeckBase):
    """演示文稿幻灯片（云端权威）"""

    __tablename__ = 'page'

    id: Mapped[id_key] = mapped_column(init=False)
    deck_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属 deck（引用 deck.deck.id）')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='owner 隔离键（冗余自 deck）')
    position: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='页序（0 起；未删页内唯一）')
    title: Mapped[str] = mapped_column(sa.String(255), default='', comment='页标题（来自 outline）')
    html: Mapped[str] = mapped_column(UniversalText, default='', comment='单页 HTML（见渲染契约）')
    notes: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='演讲者备注（可空）')
    layout_intent: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='版式意图（冗余自 outline）')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 empty/generating/generated/edited')
    render_state: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='渲染缓存')
    thumb_asset_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='该页缩略图资产 id（可空）')
    rev: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='单调版本（乐观并发 + 同步水位）')
    deleted_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='软删时间（非空=已删）')

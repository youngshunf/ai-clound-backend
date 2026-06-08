from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.deck.model._base import DeckBase
from backend.common.model import TimeZone, UniversalText, id_key


class Asset(DeckBase):
    """演示文稿资产引用（云端权威；二进制存 public.hasn_assets）"""

    __tablename__ = 'asset'

    id: Mapped[id_key] = mapped_column(init=False)
    deck_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属 deck（引用 deck.deck.id）')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='owner 隔离键')
    asset_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='云端资产 id（引用 hasn_assets）')
    kind: Mapped[str] = mapped_column(sa.String(20), default='', comment='类型 image/font/export-pptx/pdf/thumb')
    uri: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='hasn://asset 引用')
    mime: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='MIME 类型')
    size: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='字节大小')
    filename: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='文件名')
    local_path: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='离线兜底本地路径')
    deleted_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='软删时间（非空=已删）')

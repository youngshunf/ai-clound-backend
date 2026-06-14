from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_deck.model._base import DeckBase
from backend.common.model import TimeZone, UniversalText, id_key


class StyleProfile(DeckBase):
    """演示文稿可复用样式 StyleProfile（云端权威，仅 custom）"""

    __tablename__ = 'style_profile'

    id: Mapped[id_key] = mapped_column(init=False)
    slug: Mapped[str] = mapped_column(sa.String(64), default='', comment='样式 slug（同 owner 下唯一；人读标识）')
    label: Mapped[str] = mapped_column(sa.String(128), default='', comment='展示名')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='描述（可空）')
    source: Mapped[str] = mapped_column(sa.String(16), default='', comment='来源 builtin/custom/override')
    design_contract: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='预设视觉契约')
    style_prompt: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='注入生成的风格提示词片段')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='owner 隔离键')
    rev: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='单调版本（乐观并发 + 同步水位）')
    deleted_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='软删时间（非空=已删）')

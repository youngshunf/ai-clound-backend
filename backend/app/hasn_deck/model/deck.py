from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_deck.model._base import DeckBase
from backend.common.model import TimeZone, UniversalText, id_key


class Deck(DeckBase):
    """演示文稿（云端权威）"""

    __tablename__ = 'deck'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属 owner HASN ID（隔离键）')
    title: Mapped[str] = mapped_column(sa.String(255), default='', comment='标题')
    topic: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='原始主题/brief（可空）')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 draft/generating/ready/archived')
    language: Mapped[str] = mapped_column(sa.String(16), default='', comment='主语言（zh/en…）')
    outline: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='大纲 OutlineItem[]（JSON）')
    design_contract: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='视觉契约（JSON）')
    style_profile_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='StyleProfile slug')
    page_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='页数冗余计数')
    cover_asset_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='封面资产 id')
    source: Mapped[str] = mapped_column(sa.String(16), default='', comment='来源 agent/manual/imported')
    # 应用平台 v3 §4.2(3) 产物级协作快路径列
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='personal', comment='归属 personal/enterprise')
    enterprise_id: Mapped[int | None] = mapped_column(sa.BigInteger(), default=None, comment='归属企业 ID（enterprise 必填）')
    visibility: Mapped[str] = mapped_column(sa.String(16), default='private', comment='可见面 private/enterprise/link')
    rev: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='单调版本（乐观并发 + 同步水位）')
    deleted_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='软删时间（非空=已删）')

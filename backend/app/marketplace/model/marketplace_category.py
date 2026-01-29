from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class MarketplaceCategory(Base):
    """技能市场分类表"""

    __tablename__ = 'marketplace_category'

    id: Mapped[id_key] = mapped_column(init=False)
    slug: Mapped[str] = mapped_column(sa.String(50), default='', comment='分类标识')
    name: Mapped[str] = mapped_column(sa.String(100), default='', comment='分类名称')
    icon: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment='emoji图标')
    parent_slug: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='父分类标识')
    sort_order: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='排序顺序')

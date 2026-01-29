from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, UniversalText


class MarketplaceSkill(Base):
    """技能市场技能表"""

    __tablename__ = 'marketplace_skill'

    id: Mapped[id_key] = mapped_column(init=False)
    skill_id: Mapped[str] = mapped_column(sa.String(100), default='', comment='技能唯一标识')
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment='技能名称')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='技能描述')
    icon_url: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='SVG图标URL')
    author_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='作者用户ID')
    author_name: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='作者名称')
    category: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='分类')
    tags: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='标签，逗号分隔')
    pricing_type: Mapped[str] = mapped_column(sa.String(20), default='', comment='定价类型 (free:免费:green/paid:付费:orange)')
    price: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='价格')
    is_private: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否私有')
    is_official: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否官方技能')
    download_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='下载次数')

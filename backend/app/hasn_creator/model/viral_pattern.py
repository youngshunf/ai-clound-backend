from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_creator.model._base import HasnCreatorAppBase
from backend.common.model import UniversalText, id_key


class ViralPattern(HasnCreatorAppBase):
    """爆款模式库；钩子/结构/标题/CTA，usage + success_rate（可全局 project_id NULL）"""

    __tablename__ = 'viral_pattern'

    id: Mapped[id_key] = mapped_column(init=False)
    project_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='归属项目（可空=全局通用）')
    user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment=None)
    pattern_type: Mapped[str] = mapped_column(sa.String(24), default='', comment='类型 (hook:钩子:blue/structure:结构:purple/title:标题:orange/cta:行动号召:green)')
    template: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='模板（如「3 步搞定 X」标题模板）')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    example: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    usage_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    success_rate: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment=None)
    source: Mapped[str] = mapped_column(sa.String(20), default='', comment='来源 (ai_extracted:AI提炼:violet/manual:手动:blue/builtin:内置:gray)')
    tags: Mapped[list[str]] = mapped_column(postgresql.JSONB(), default_factory=list, comment=None)
    is_builtin: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment=None)

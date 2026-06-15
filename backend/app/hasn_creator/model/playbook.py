import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import UniversalText, id_key
from backend.app.hasn_creator.model._base import HasnCreatorAppBase


class Playbook(HasnCreatorAppBase):
    """账号打法模板（内容策略 + 节奏 + 红线），内置 + 自定义（设计 §5.2 / §7）"""

    __tablename__ = 'playbook'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='归属主人（可空=内置 playbook）')
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='归属企业（可空）')
    name: Mapped[str] = mapped_column(sa.String(100), default='', comment=None)
    goal: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='打法目标')
    content_strategy: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='内容策略（支柱配比/形态偏好/选题方向）'
    )
    cadence: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='发布节奏 {frequency,best_time}'
    )
    tone_guide: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='调性指南')
    red_lines: Mapped[list] = mapped_column(postgresql.JSONB(), default_factory=list, comment='红线/禁区（合规硬过滤）')
    is_builtin: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=False, comment=None)

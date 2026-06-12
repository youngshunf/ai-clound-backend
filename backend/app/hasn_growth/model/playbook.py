from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText
from backend.app.hasn_growth.model._base import HasnGrowthAppBase


class Playbook(HasnGrowthAppBase):
    """获客打法模板（目标画像 + 触达节奏 + 话术要点），内置 + 自定义"""

    __tablename__ = 'playbook'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='归属主人（可空=内置 playbook）')
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment=None)
    enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment=None)
    goal: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    target_profile: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    cadence: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='触达节奏 [{day,channel,goal}]')
    tone_guide: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    exit_rule: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='止损规则 {max_silent_rounds,action}')
    is_builtin: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment=None)

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import UniversalText, id_key


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
    # 企业化双模归属（GE1，设计 v3 §6.7）：内置(is_builtin)对所有人可见；enterprise playbook 仅本企业。
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='personal', comment='归属模式 (personal:个人:blue/enterprise:企业:purple)')
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='企业 ID（enterprise 模式；personal/内置 为 NULL）')

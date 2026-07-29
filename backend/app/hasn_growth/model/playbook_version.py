import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import UniversalText, id_key


class PlaybookVersion(HasnGrowthAppBase):
    """获客打法不可变版本快照，历史执行只读取本表"""

    __tablename__ = 'playbook_version'

    id: Mapped[id_key] = mapped_column(init=False)
    playbook_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    version: Mapped[int] = mapped_column(sa.INTEGER(), default=1, comment=None)
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment=None)
    goal: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    target_profile: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    cadence: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    tone_guide: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    exit_rule: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    definition_hash: Mapped[str] = mapped_column(
        sa.String(64), default='', comment='规范化打法定义 SHA256，用于版本幂等与审计'
    )
    created_by_kind: Mapped[str] = mapped_column(sa.String(16), default='system', comment=None)
    created_by_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)

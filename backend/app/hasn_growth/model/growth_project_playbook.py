from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import id_key


class GrowthProjectPlaybook(HasnGrowthAppBase):
    """获客漏斗采用的打法版本与项目级配置快照"""

    __tablename__ = 'growth_project_playbook'

    id: Mapped[id_key] = mapped_column(init=False)
    growth_project_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment=None)
    playbook_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    playbook_version: Mapped[int] = mapped_column(sa.INTEGER(), default=1, comment=None)
    status: Mapped[str] = mapped_column(sa.String(16), default='active', comment=None)
    configuration_snapshot: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)

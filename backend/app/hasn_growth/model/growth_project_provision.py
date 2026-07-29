from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, id_key


class GrowthProjectProvision(HasnGrowthAppBase):
    """建漏斗、建库、挂靠和建站步骤的可靠编排状态"""

    __tablename__ = 'growth_project_provision'

    id: Mapped[id_key] = mapped_column(init=False)
    growth_project_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment=None)
    command_id: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)
    idempotency_key: Mapped[str] = mapped_column(sa.String(200), default='', comment=None)
    step: Mapped[str] = mapped_column(sa.String(48), default='', comment=None)
    status: Mapped[str] = mapped_column(sa.String(16), default='pending', comment=None)
    attempts: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    next_retry_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    last_error: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment=None)
    started_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    finished_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)

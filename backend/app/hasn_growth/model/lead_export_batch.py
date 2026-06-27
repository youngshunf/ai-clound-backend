from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText, TimeZone
from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.utils.timezone import timezone


class LeadExportBatch(HasnGrowthAppBase):
    """Lead CSV export batch"""

    __tablename__ = 'export_batch'

    id: Mapped[id_key] = mapped_column(init=False)
    batch_no: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    filter_payload: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    format: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    total_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    file_path: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment=None)
    file_sha256: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    error_message: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    started_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    finished_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)

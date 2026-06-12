from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key
from backend.app.hasn_growth.model._base import HasnGrowthAppBase


class LeadExportItem(HasnGrowthAppBase):
    """Lead CSV export item snapshot"""

    __tablename__ = 'export_item'

    id: Mapped[id_key] = mapped_column(init=False)
    batch_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    lead_contact_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    lead_no: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    snapshot: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)

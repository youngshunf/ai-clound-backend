import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import id_key


class OptoutRecord(HasnGrowthAppBase):
    """获客退订/勿扰登记（合规硬约束，outreach.send 入口硬查，命中即 blocked_optout）"""

    __tablename__ = 'optout_record'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    channel: Mapped[str] = mapped_column(sa.String(24), default='', comment='渠道；all=全渠道')
    address_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment='sha256(归一化联系方式)——不存明文')
    customer_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    reason: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment=None)
    source: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)

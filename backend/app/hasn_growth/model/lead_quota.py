import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import id_key


class LeadQuota(HasnGrowthAppBase):
    """线索领取额度账本（doc93 §4.2：免费额度按月重置 + 购买余额永不过期·线索付费不走积分）"""

    __tablename__ = 'lead_quota'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int] = mapped_column(sa.BigInteger(), default=0, comment='用户 ID（每用户一行）')
    period_key: Mapped[str] = mapped_column(
        sa.String(7), default='', comment='免费额度归属月 YYYY-MM（与当前月不一致即归零 free_used）'
    )
    free_used: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='本月已用免费额度（领取的免费线索数）')
    purchased_balance: Mapped[int] = mapped_column(
        sa.INTEGER(), default=0, comment='购买的可领取线索余额（永不过期·支付到账增加·领取扣减）'
    )
    purchased_total: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='累计购买线索数（对账）')
    consumed_total: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='累计已领取线索数（免费+购买·对账）')

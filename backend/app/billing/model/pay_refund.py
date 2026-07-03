from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import BillingBase
from backend.common.model import TimeZone, id_key


class PayRefund(BillingBase):
    """退款记录表"""

    __tablename__ = 'pay_refund'

    id: Mapped[id_key] = mapped_column(init=False)
    # 必填字段在前
    refund_no: Mapped[str] = mapped_column(sa.String(64), unique=True, comment='退款单号')
    order_no: Mapped[str] = mapped_column(sa.String(64), comment='关联订单号')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), comment='用户 ID')
    refund_amount: Mapped[int] = mapped_column(sa.BIGINT(), comment='退款金额（分）')
    # 有默认值字段在后
    channel_code: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='渠道编码')
    reason: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='退款原因')
    channel_refund_no: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='第三方退款单号')
    status: Mapped[int] = mapped_column(sa.SMALLINT(), default=0, comment='状态 0=待处理 1=成功 2=失败')
    success_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='退款成功时间')

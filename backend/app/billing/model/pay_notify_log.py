import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import BillingBase
from backend.common.model import UniversalText, id_key


class PayNotifyLog(BillingBase):
    """支付回调日志表"""

    __tablename__ = 'pay_notify_log'

    id: Mapped[id_key] = mapped_column(init=False)
    # 必填字段在前
    notify_type: Mapped[str] = mapped_column(sa.String(16), comment='通知类型 pay/refund/contract')
    # 有默认值字段在后
    order_no: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='关联订单号')
    channel_code: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='渠道编码')
    notify_data: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='原始回调数据')
    status: Mapped[int] = mapped_column(sa.SMALLINT(), default=0, comment='状态 0=待处理 1=成功 2=失败')
    error_msg: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='错误信息')

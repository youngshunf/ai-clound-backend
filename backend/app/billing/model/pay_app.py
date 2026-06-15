import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import BillingBase
from backend.common.model import id_key


class PayApp(BillingBase):
    """支付应用配置表"""

    __tablename__ = 'pay_app'

    id: Mapped[id_key] = mapped_column(init=False)
    app_key: Mapped[str] = mapped_column(sa.String(64), unique=True, comment='应用标识（如 huanxing）')
    name: Mapped[str] = mapped_column(sa.String(64), comment='应用名称')
    order_notify_url: Mapped[str] = mapped_column(sa.String(1024), comment='支付成功回调地址')
    status: Mapped[int] = mapped_column(sa.SMALLINT(), default=1, comment='状态 1=启用 0=停用')
    remark: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='备注')
    refund_notify_url: Mapped[str | None] = mapped_column(sa.String(1024), default=None, comment='退款回调地址')

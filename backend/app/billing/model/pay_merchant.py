import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.billing.model._base import BillingBase
from backend.common.model import id_key


class PayMerchant(BillingBase):
    """支付商户表 — 微信/支付宝账户"""

    __tablename__ = 'pay_merchant'

    id: Mapped[id_key] = mapped_column(init=False)
    name: Mapped[str] = mapped_column(sa.String(100), comment='商户名称')
    type: Mapped[str] = mapped_column(sa.String(20), comment='商户类型 weixin/alipay')
    config: Mapped[dict] = mapped_column(sa.JSON(), default=dict, comment='商户核心配置 JSON')
    status: Mapped[int] = mapped_column(sa.SMALLINT(), default=1, comment='状态 0停用 1启用')
    remark: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='备注')

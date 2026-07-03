from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import APP_SCHEMA, BillingBase
from backend.common.model import id_key


class PayChannel(BillingBase):
    """支付渠道配置表 — 挂在商户下"""

    __tablename__ = 'pay_channel'
    __table_args__ = (
        sa.UniqueConstraint('code', name='uq_pay_channel_code'),
        {'comment': '支付渠道配置表', 'schema': APP_SCHEMA},
    )

    id: Mapped[id_key] = mapped_column(init=False)
    code: Mapped[str] = mapped_column(sa.String(32), comment='渠道编码 wx_native/alipay_pc 等')
    name: Mapped[str] = mapped_column(sa.String(64), comment='渠道显示名称')
    # 以下字段都有默认值
    merchant_id: Mapped[int | None] = mapped_column(sa.BIGINT(), sa.ForeignKey(f'{APP_SCHEMA}.pay_merchant.id'), default=None, comment='关联商户 ID')
    status: Mapped[int] = mapped_column(sa.SMALLINT(), default=1, comment='状态 1=启用 0=停用')
    fee_rate: Mapped[Decimal] = mapped_column(sa.NUMERIC(6, 4), default=Decimal(0), comment='费率')
    remark: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='备注')
    config: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='旧配置（已迁移到 merchant）')
    extra_config: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='渠道特有配置 JSON')

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import APP_SCHEMA, BillingBase
from backend.common.model import TimeZone, id_key


class PayOrder(BillingBase):
    """支付订单表"""

    __tablename__ = 'pay_order'

    id: Mapped[id_key] = mapped_column(init=False)
    # 必填字段（无默认值）在前
    order_no: Mapped[str] = mapped_column(sa.String(64), unique=True, comment='商户订单号（HX + 时间戳 + 随机数）')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), index=True, comment='用户 ID')
    order_type: Mapped[str] = mapped_column(sa.String(32), comment='订单类型 subscribe/auto_renew/credit_pack/upgrade')
    subject: Mapped[str] = mapped_column(sa.String(128), comment='订单标题')
    amount: Mapped[int] = mapped_column(sa.BIGINT(), comment='原价（分）')
    pay_amount: Mapped[int] = mapped_column(sa.BIGINT(), comment='实付金额（分）')
    expire_time: Mapped[datetime] = mapped_column(TimeZone, comment='订单过期时间')

    # 有默认值的字段在后
    channel_id: Mapped[int | None] = mapped_column(sa.BIGINT(), sa.ForeignKey(f'{APP_SCHEMA}.pay_channel.id'), default=None, comment='关联支付渠道 ID')
    channel_code: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='渠道编码（冗余）')
    body: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='订单描述')
    target_tier: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='目标套餐')
    billing_cycle: Mapped[str | None] = mapped_column(sa.String(16), default=None, comment='计费周期 monthly/yearly')
    discount_amount: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='优惠金额（分）')
    refund_amount: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='已退款金额（分）')
    status: Mapped[int] = mapped_column(sa.SMALLINT(), default=0, index=True, comment='状态 0=待支付 1=已支付 2=已退款 3=已关闭 4=已过期')
    user_ip: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='用户 IP')
    channel_order_no: Mapped[str | None] = mapped_column(sa.String(128), default=None, index=True, comment='第三方交易号')
    channel_user_id: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='第三方用户标识')
    success_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='支付成功时间')
    extra_data: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='扩展数据')
    # 统一商业化内核（MK-1）：商品目录引用快照，下单入口按 kind 分发 fulfillment（收编散落 order_type）
    offering_ref: Mapped[dict | None] = mapped_column(
        postgresql.JSONB(),
        default=None,
        comment='商品目录引用快照（{offering_key,plan_key,kind}；支付成功回调按 kind 分发履约）',
    )

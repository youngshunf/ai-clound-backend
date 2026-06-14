from datetime import date, datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import BillingBase
from backend.common.model import id_key, TimeZone


class PayContract(BillingBase):
    """签约记录表（自动续费核心）"""

    __tablename__ = 'pay_contract'

    id: Mapped[id_key] = mapped_column(init=False)
    # 必填字段（无默认值）在前
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), index=True, comment='用户 ID')
    channel_code: Mapped[str] = mapped_column(sa.String(32), comment='渠道编码 wx_papay/alipay_cycle')
    contract_no: Mapped[str] = mapped_column(sa.String(128), unique=True, comment='商户侧签约协议号')
    tier: Mapped[str] = mapped_column(sa.String(32), comment='签约套餐 free/pro/advanced/flagship')
    billing_cycle: Mapped[str] = mapped_column(sa.String(16), comment='计费周期 monthly/yearly')
    deduct_amount: Mapped[int] = mapped_column(sa.BIGINT(), comment='每期扣款金额（分）')

    # 有默认值的字段在后
    channel_contract_id: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='第三方签约协议号（微信 contract_id / 支付宝 agreement_no）')
    plan_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='签约模板 ID（微信 plan_id）')
    status: Mapped[int] = mapped_column(sa.SMALLINT(), default=0, index=True, comment='状态 0=签约中 1=已签约 2=已解约 3=签约失败')
    signed_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='签约成功时间')
    terminated_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='解约时间')
    terminate_reason: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='解约原因')
    next_deduct_date: Mapped[date | None] = mapped_column(sa.DATE(), default=None, index=True, comment='下次扣款日期')
    last_deduct_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='上次扣款时间')
    deduct_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='累计成功扣款次数')
    extra_data: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='扩展数据（如微信 openid 等）')

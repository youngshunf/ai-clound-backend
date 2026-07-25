from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import BillingBase
from backend.common.model import id_key


class BillingPlan(BillingBase):
    """商品档位（价格+配额快照+试用/宽限策略）"""

    __tablename__ = 'billing_plan'

    id: Mapped[id_key] = mapped_column(init=False)
    offering_key: Mapped[str] = mapped_column(sa.String(64), default='', comment='所属 offering 业务键（指向 billing_offering.key）')
    plan_key: Mapped[str] = mapped_column(sa.String(64), default='', comment='档位键（如 monthly/yearly/standard/once）')
    price_amount: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='价格（price_unit 单位，如元；改价只影响新购续费）')
    price_unit: Mapped[str] = mapped_column(sa.String(16), default='', comment='计价单位 (cny:人民币元:blue/credits:积分:cyan)')
    cycle: Mapped[str] = mapped_column(sa.String(16), default='', comment='计费周期 (once:一次买断:gray/month:月:blue/year:年:green)')
    quota_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='配额包快照（站点数/内存/卷/席位数/max_agents…；购买时固化进权益行）')
    trial_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='试用策略（enabled/days/times）')
    grace_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='宽限策略（remind_days/grace_days，到期提醒节奏+宽限天数）')
    # doc94 D1：subscription_tier / credit_package 要被删除，其展示字段迁到这里，
    # 定价页与积分包列表改从商品目录取，不再回落 legacy 表。
    display_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='展示字段快照（display_name/features/description 等）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (active:上架:green/inactive:下架:gray)')
    sort_order: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='排序权重')

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import BillingBase
from backend.common.model import TimeZone, id_key
from backend.utils.timezone import timezone


class UserSubscription(BillingBase):
    """用户订阅表"""

    __tablename__ = 'user_subscription'

    id: Mapped[id_key] = mapped_column(init=False)
    app_code: Mapped[str] = mapped_column(sa.String(32), default='huanxing', comment='应用标识 (huanxing:唤星/zhixiaoya:知小鸦)')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='用户 ID')
    tier: Mapped[str] = mapped_column(sa.String(32), default='', comment='订阅等级 (free:免费版/basic:基础版/pro:专业版/enterprise:企业版)')
    subscription_type: Mapped[str] = mapped_column(sa.String(16), default='monthly', comment='订阅类型 (monthly:月度/yearly:年度)')
    monthly_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='每月积分配额')
    current_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='当前剩余积分')
    used_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='本周期已使用积分')
    purchased_credits: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=None, comment='购买的额外积分')
    billing_cycle_start: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='计费周期开始时间')
    billing_cycle_end: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='计费周期结束时间')
    subscription_start_date: Mapped[datetime | None] = mapped_column(TimeZone, default=None, nullable=True, comment='订阅开始时间')
    subscription_end_date: Mapped[datetime | None] = mapped_column(TimeZone, default=None, nullable=True, comment='订阅结束时间')
    next_grant_date: Mapped[datetime | None] = mapped_column(TimeZone, default=None, nullable=True, comment='下次赠送积分时间 (年度订阅专用)')
    status: Mapped[str] = mapped_column(sa.String(32), default='', comment='订阅状态 (active:激活/expired:已过期/cancelled:已取消)')
    auto_renew: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='只表示是否尝试创建下一张续费订单，不代表自动发额度')
    # ── 商业合同字段（doc94 C1）────────────────────────────────────────────
    # 本表从「兼任余额表」改成纯商业合同表：管「买了什么、何时生效、何时到期」，
    # 不管「还剩多少积分」——余额只有 NewAPI 一个权威。
    contract_no: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='稳定合同号（幂等键组件）')
    offering_key: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='商品目录引用')
    plan_key: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='商品档位引用')
    contract_start_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='合同起始时间')
    contract_end_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='合同结束时间（免费档为空，表示无商业到期）')
    # 周期恒为 30 天（含免费档）：它定义「多久清零重置一次」，绝不使用自然月。
    cycle_seconds: Mapped[int] = mapped_column(sa.BIGINT(), default=2592000, comment='周期长度秒数，恒为 2592000（30 天）')
    # 免费档为空表示无限期循环，没有第 N 期后的合同终点。
    cycle_count: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='周期数：月付 1、年付 12、免费档为空')
    plan_snapshot: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='购买时固化的合同参数')
    source_order_no: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='创建本合同的支付订单号（免费档可空）')
    external_subscription_id: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='NewAPI 投影标识（不是余额）')
    fulfillment_status: Mapped[str] = mapped_column(
        sa.String(16), default='not_required', comment='履约状态 not_required/pending/processing/succeeded/retrying/dead'
    )
    free_policy_version: Mapped[int] = mapped_column(sa.INTEGER(), default=1, comment='免费政策版本，随免费政策变更递增')
    # 每次「失效→重新授予」+1。缺它则免费政策撤销后，用户此生再也发不出第二次免费额度：
    # 旧幂等键会把新的授予当成重放永久挡住。
    free_grant_epoch: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='免费额度授予轮次')
    max_agents: Mapped[int] = mapped_column(sa.INTEGER(), default=1, comment='Agent 最大数量（跨服务器总计）')

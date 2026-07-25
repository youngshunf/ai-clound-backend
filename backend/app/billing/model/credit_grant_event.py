from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import BillingBase
from backend.common.model import TimeZone, UniversalText, id_key


class CreditGrantEvent(BillingBase):
    """履约事件表（事务 outbox + 云端审计，不保存权威余额）"""

    __tablename__ = 'credit_grant_event'

    id: Mapped[id_key] = mapped_column(init=False)
    event_id: Mapped[str] = mapped_column(sa.String(36), default='', comment='投递给 NewAPI 的 event_id（UUID 字符串，全局唯一；超时重投必须复用同一个，禁止换 ID 重发）')
    idempotency_key: Mapped[str] = mapped_column(sa.String(160), default='', comment='业务幂等键（取自固定全集，不得现场自创）')
    event_type: Mapped[str] = mapped_column(sa.String(32), default='', comment='事件类型 (wallet_grant:钱包发放:green/wallet_revoke:钱包回收:orange/subscription_activate:订阅生效:blue/subscription_expire:订阅到期:grey)')
    app_code: Mapped[str] = mapped_column(sa.String(32), default='', comment='应用标识')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='唤星用户 ID')
    newapi_user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='履约目标 NewAPI 用户 ID（快照）')
    order_no: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='关联支付订单号')
    refund_no: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='关联退款单号')
    subscription_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='关联订阅合同主键')
    contract_no: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='关联订阅合同号')
    credit_amount: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, comment='不可变的发放/回收参数积分数（不是余额）')
    applied_credits: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, comment='NewAPI 回执的实际入账/回收积分（审计以此为准）')
    payload: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='投递给 NewAPI 的请求快照')
    payload_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment='投递载荷指纹，用于冲突检测')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (pending:待投递:blue/processing:投递中:orange/succeeded:已完成:green/retrying:重试中:orange/dead:死信:red/cancelled:已取消:grey)')
    attempt_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='已投递尝试次数')
    next_attempt_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='下次投递时间（指数退避 + 抖动）')
    last_error_code: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最后一次失败的机器错误码')
    last_error_message: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='最后一次失败原因（敏感值已脱敏）')
    response_snapshot: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='NewAPI 回执快照，仅供排障，不得用于判余额')
    delivered_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='投递成功时间')

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key


class HasnTradeSessions(Base):
    """HASN 交易会话表"""

    __tablename__ = 'hasn_trade_sessions'

    id: Mapped[id_key] = mapped_column(init=False)
    buyer_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='买方 hasn_id')
    seller_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='卖方 hasn_id')
    relation_type: Mapped[str] = mapped_column(sa.String(20), default='', comment='关系类型 (commerce:商业:orange/service:履约:green)')
    scope: Mapped[str] = mapped_column(sa.String(30), default='', comment='当前作用域 (commerce: pre_sale:售前/negotiation:协商/in_order:订单中/fulfilling:履约中/after_sale:售后/subscription:订阅 | service: active_order:活跃订单 | professional: consultation:咨询/treatment:进行中/follow_up:跟进 | platform: app_installation:应用安装/system_notice:系统通知)')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (active:进行中:green/completed:已完成:blue/archived:已归档:gray/cancelled:已取消:red)')
    lifecycle_state: Mapped[str] = mapped_column(
        sa.String(16),
        default='active',
        server_default='active',
        comment='作用域生命周期 (pending:待激活:gray/active:激活:violet/closed:已关闭:neutral/expired:已过期:red)',
    )
    order_id: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='关联订单 ID')
    expires_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='过期时间')
    meta_data: Mapped[dict] = mapped_column('metadata', postgresql.JSONB(), default_factory=dict, comment='附加元数据 (JSONB)')

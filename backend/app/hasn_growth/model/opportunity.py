from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class Opportunity(HasnGrowthAppBase):
    """获客商机（阶段推进 + 金额 + 成交/败因登记）"""

    __tablename__ = 'opportunity'

    id: Mapped[id_key] = mapped_column(init=False)
    opportunity_no: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    customer_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    growth_project_id: Mapped[UUID | None] = mapped_column(
        sa.UUID(), default=None, comment='获客漏斗 UUID（迁移期可空）'
    )
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment=None)
    version: Mapped[int] = mapped_column(
        sa.BIGINT(),
        default=1,
        comment='并发控制版本；每次阶段变化或关闭单调递增',
    )
    stage: Mapped[str] = mapped_column(
        sa.String(24),
        default='',
        comment=(
            '阶段 (contacted:已触达:blue/replied:已回应:cyan/proposal:已发提案:purple/'
            'negotiation:商务洽谈:orange/closed_won:成交:green/closed_lost:流失:red)'
        ),
    )
    amount: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, comment=None)
    currency: Mapped[str] = mapped_column(sa.String(8), default='', comment=None)
    probability: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, comment=None)
    expected_close_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    won_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    lost_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    lost_reason: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment=None)
    close_note: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    review_task_id: Mapped[str | None] = mapped_column(
        sa.String(64),
        default=None,
        comment='成交或流失后幂等创建的复盘任务 UUID',
    )
    created_by_kind: Mapped[str] = mapped_column(
        sa.String(16), default='', comment='创建者 (owner:主人:blue/agent:分身:violet)'
    )
    # 企业化双模归属（GE1，设计 v3 §6.7）。
    owner_scope: Mapped[str] = mapped_column(
        sa.String(16), default='personal', comment='归属模式 (personal:个人:blue/enterprise:企业:purple)'
    )
    enterprise_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='企业 ID（enterprise 模式；personal 为 NULL）'
    )
    assignee: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='负责人 hasn_id（enterprise 模式）'
    )

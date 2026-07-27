from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, id_key


class Customer(HasnGrowthAppBase):
    """获客客户（qualified 线索 / inbound 直建）"""

    __tablename__ = 'customer'

    id: Mapped[id_key] = mapped_column(init=False)
    customer_no: Mapped[str] = mapped_column(sa.String(40), default='', comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    lead_contact_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    source_kind: Mapped[str] = mapped_column(sa.String(24), default='', comment='来源 (outbound_crawl:采集:blue/inbound_form:留资:green/community:内容:purple/manual:手动:gray)')
    company_name: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    contact_name: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment=None)
    email: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    phone: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment=None)
    wechat: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment=None)
    im_refs: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    profile_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='AI 画像（行业/规模/痛点/预算信号/决策角色/沟通偏好）')
    intent_score: Mapped[Decimal] = mapped_column(sa.NUMERIC(), default=0, comment='意向分（分身每次跟进后更新）')
    lifecycle_status: Mapped[str] = mapped_column(sa.String(16), default='', comment='生命周期 (active:跟进中:blue/engaged:有回应:cyan/opportunity:已立商机:purple/silent:沉默:gray/won:成交:green/lost:流失:red/archived:归档:gray)')
    owner_agent_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    followup_task_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='当前跟进任务（hasn_task.task.id 逻辑引用）')
    tags: Mapped[list[str]] = mapped_column(postgresql.JSONB(), default_factory=list, comment=None)
    last_activity_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    next_followup_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    last_followup_trigger_at: Mapped[datetime | None] = mapped_column(
        TimeZone, default=None, comment='J3 即时跟进 run_now 上次触发时刻（云端 10 分钟防抖窗口）'
    )
    silent_round_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    # 企业化双模归属（GE1，设计 v3 §6.7）：personal→holder=user_id；enterprise→holder=enterprise_id+assignee。
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='personal', comment='归属模式 (personal:个人:blue/enterprise:企业:purple)')
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='企业 ID（enterprise 模式；personal 为 NULL）')
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='负责人 hasn_id（enterprise 模式跟进人；与 owner_agent_id 分身维度并存）')

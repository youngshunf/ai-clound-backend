import uuid

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone


class GrowthProject(HasnGrowthAppBase):
    """平台项目唯一挂靠的获客漏斗"""

    __tablename__ = 'growth_project'

    id: Mapped[UUID] = mapped_column(sa.UUID(), primary_key=True, default=uuid.uuid4, init=False)
    platform_project_id: Mapped[UUID] = mapped_column(
        sa.UUID(), default=None, comment='平台项目云端权威 UUID，一个平台项目至多一个获客漏斗'
    )
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_hasn_id: Mapped[str] = mapped_column(
        sa.String(40), default='', comment='主人稳定 HASN ID，由服务端从平台项目和鉴权上下文解析'
    )
    owner_scope: Mapped[str] = mapped_column(
        sa.String(16), default='personal', comment='归属模式 (personal:个人:blue/enterprise:企业:purple)'
    )
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment=None)
    tagline: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment=None)
    product_profile: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    icp_profile: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    profile_version: Mapped[int] = mapped_column(sa.INTEGER(), default=1, comment=None)
    profile_source_hash: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    profile_updated_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    kb_ref: Mapped[str | None] = mapped_column(
        sa.String(255), default=None, comment=f'知识库资源引用 hasn://knowledge/kbs/{id}'
    )
    landing_site_ref: Mapped[str | None] = mapped_column(
        sa.String(255), default=None, comment=f'站点资源引用 hasn://publish/sites/{id}'
    )
    owner_agent_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment=None)
    status: Mapped[str] = mapped_column(
        sa.String(16),
        default='draft',
        comment='状态 (draft:草稿:gray/active:运行中:green/paused:已暂停:orange/archived:已归档:gray)',
    )
    provision_status: Mapped[str] = mapped_column(
        sa.String(16),
        default='pending',
        comment='开通状态 (pending:待开始:gray/running:进行中:blue/ready:就绪:green/failed:失败:red)',
    )
    provision_error: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment=None)
    monthly_budget: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, comment=None)
    budget_currency: Mapped[str] = mapped_column(sa.String(3), default='CNY', comment=None)
    quiet_hours_start: Mapped[int] = mapped_column(
        sa.SMALLINT(),
        default=21,
        comment='静默时段开始小时，使用项目时区的 0–23 整点',
    )
    quiet_hours_end: Mapped[int] = mapped_column(
        sa.SMALLINT(),
        default=9,
        comment='静默时段结束小时，使用项目时区的 0–23 整点',
    )
    daily_outreach_limit: Mapped[int] = mapped_column(
        sa.INTEGER(),
        default=20,
        comment='项目每日发送成功或人工发送证明的触达上限',
    )
    policy_version: Mapped[int] = mapped_column(
        sa.INTEGER(),
        default=1,
        comment='渠道、静默时段、频控和预算策略版本',
    )
    readiness_snapshot: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    stats_snapshot: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)

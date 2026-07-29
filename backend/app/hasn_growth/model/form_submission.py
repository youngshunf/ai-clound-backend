from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class FormSubmission(HasnGrowthAppBase):
    """获客落地页表单回流（inbound 线索缓冲区）"""

    __tablename__ = 'form_submission'

    id: Mapped[id_key] = mapped_column(init=False)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    growth_project_id: Mapped[UUID | None] = mapped_column(
        sa.UUID(), default=None, comment='获客漏斗 UUID（迁移期可空）'
    )
    platform_project_id: Mapped[UUID | None] = mapped_column(
        sa.UUID(), default=None, comment='平台项目云端权威 UUID（迁移期可空）'
    )
    publish_ref: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment=None)
    publish_site_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='发布站点云端权威 ID')
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='站点范围幂等键')
    submission_fingerprint: Mapped[str | None] = mapped_column(
        sa.String(128), default=None, comment='规范化请求的带密钥指纹'
    )
    payload: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    email: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    phone: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment=None)
    name: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment=None)
    company: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    status: Mapped[str] = mapped_column(
        sa.String(16),
        default='',
        comment='状态 (pending:待处理:gray/converted:已转化:green/rejected:已拒绝:red/spam:垃圾:red)',
    )
    customer_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    lead_contact_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='全局联系人事实 ID')
    project_lead_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='项目线索 ID')
    contact_private_profile_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='当前主体私有联系人资料 ID'
    )
    contact_channel_ids: Mapped[list[int]] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment='当前主体私有联系方式 ID 列表'
    )
    privacy_notice_version: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='提交时展示的隐私说明版本'
    )
    consent_purpose: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='同意用途')
    consent_source: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='同意来源')
    consent_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='同意时间')
    ip_hmac: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='IP 带密钥指纹')
    spam_status: Mapped[str | None] = mapped_column(sa.String(16), default='unchecked', comment='反滥用状态')
    spam_reason: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='反滥用判定原因')
    utm_source: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    utm_medium: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    utm_campaign: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    utm_content: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    utm_term: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    referrer: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    task_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='表单处理任务 ID')
    source_meta: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='UTM/referrer/IP hash（反滥用 + 归因）'
    )
    # 企业化双模归属（GE1，设计 v3 §6.7）：inbound 留资归企业池/分配。
    owner_scope: Mapped[str] = mapped_column(
        sa.String(16), default='personal', comment='归属模式 (personal:个人:blue/enterprise:企业:purple)'
    )
    enterprise_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='企业 ID（enterprise 模式；personal 为 NULL）'
    )
    assignee: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='负责人 hasn_id（enterprise 模式）'
    )

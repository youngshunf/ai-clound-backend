from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class OutreachMessage(HasnGrowthAppBase):
    """获客触达消息（出/入双向，审批状态机核心表）"""

    __tablename__ = 'outreach_message'

    id: Mapped[id_key] = mapped_column(init=False)
    customer_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    opportunity_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    growth_project_id: Mapped[UUID | None] = mapped_column(
        sa.UUID(), default=None, comment='获客漏斗 UUID（迁移期可空）'
    )
    growth_project_playbook_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='执行时项目打法采用关系 ID'
    )
    playbook_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='执行时打法 ID'
    )
    playbook_version: Mapped[int | None] = mapped_column(
        sa.INTEGER(), default=None, comment='执行时打法版本'
    )
    agent_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    direction: Mapped[str] = mapped_column(sa.String(8), default='', comment='方向 (outbound:出:blue/inbound:入:green)')
    channel: Mapped[str] = mapped_column(
        sa.String(24),
        default='',
        comment=(
            '渠道 (manual_assist:人工辅助:gray/wechat:微信:green/qq:QQ:blue/'
            'feishu:飞书:cyan/email:邮件:orange/hasn_dm:站内:purple)'
        ),
    )
    subject: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment=None)
    content: Mapped[str] = mapped_column(UniversalText, default='', comment=None)
    content_assets: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    status: Mapped[str] = mapped_column(
        sa.String(24),
        default='',
        comment=(
            '状态 (draft:草稿:gray/pending_approval:待审批:orange/approved:已批准:blue/'
            'sending:发送中:cyan/sent:已发送:green/replied:已回复:purple/rejected:已拒绝:red/'
            'failed:失败:red/blocked_optout:退订拦截:red/blocked_compliance:合规拦截:red)'
        ),
    )
    intent_note: Mapped[str | None] = mapped_column(
        sa.String(500), default=None, comment='给主人看的一句话：为什么现在发这条'
    )
    approval_user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    approved_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    reject_reason: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment=None)
    auto_approved: Mapped[bool] = mapped_column(
        sa.BOOLEAN(), default=False, comment='白名单放行标记（默认关闭，审计区分人批/自动）'
    )
    approval_status: Mapped[str | None] = mapped_column(
        sa.String(24), default='draft', comment='审批态（存量迁移完成前可空）'
    )
    delivery_status: Mapped[str | None] = mapped_column(
        sa.String(32), default='not_queued', comment='投递态（存量迁移完成前可空）'
    )
    approval_version: Mapped[int | None] = mapped_column(sa.INTEGER(), default=1, comment='审批对应内容版本')
    content_version: Mapped[int | None] = mapped_column(sa.INTEGER(), default=1, comment='内容版本')
    manual_attested_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='人工完成证明时间')
    manual_attested_by: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='人工证明操作人')
    manual_attested_channel: Mapped[str | None] = mapped_column(
        sa.String(24), default=None, comment='人工证明使用的渠道'
    )
    task_run_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    workflow_run_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    sent_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    replied_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    error_message: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    compliance_check: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    dedupe_key: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    # 企业化双模归属（GE1，设计 v3 §6.7）：审批永远归 assignee 的主人维度。
    owner_scope: Mapped[str] = mapped_column(
        sa.String(16), default='personal', comment='归属模式 (personal:个人:blue/enterprise:企业:purple)'
    )
    enterprise_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='企业 ID（enterprise 模式；personal 为 NULL）'
    )
    assignee: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='负责人 hasn_id（enterprise 模式，审批归其主人）'
    )

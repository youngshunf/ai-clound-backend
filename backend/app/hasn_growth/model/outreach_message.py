from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText, TimeZone
from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.utils.timezone import timezone


class OutreachMessage(HasnGrowthAppBase):
    """获客触达消息（出/入双向，审批状态机核心表）"""

    __tablename__ = 'outreach_message'

    id: Mapped[id_key] = mapped_column(init=False)
    customer_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    opportunity_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    agent_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    direction: Mapped[str] = mapped_column(sa.String(8), default='', comment='方向 (outbound:出:blue/inbound:入:green)')
    channel: Mapped[str] = mapped_column(sa.String(24), default='', comment='渠道 (manual_assist:人工辅助:gray/wechat:微信:green/qq:QQ:blue/feishu:飞书:cyan/email:邮件:orange/hasn_dm:站内:purple)')
    subject: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment=None)
    content: Mapped[str] = mapped_column(UniversalText, default='', comment=None)
    content_assets: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    status: Mapped[str] = mapped_column(sa.String(24), default='', comment='状态 (draft:草稿:gray/pending_approval:待审批:orange/approved:已批准:blue/sending:发送中:cyan/sent:已发送:green/replied:已回复:purple/rejected:已拒绝:red/failed:失败:red/blocked_optout:退订拦截:red/blocked_compliance:合规拦截:red)')
    intent_note: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='给主人看的一句话：为什么现在发这条')
    approval_user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    approved_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    reject_reason: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment=None)
    auto_approved: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='白名单放行标记（审计区分人批/自动）')
    task_run_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    workflow_run_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    sent_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    replied_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    error_message: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    compliance_check: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    dedupe_key: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)

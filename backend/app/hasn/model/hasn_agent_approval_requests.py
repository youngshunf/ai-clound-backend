from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class HasnAgentApprovalRequests(Base):
    """HASN Agent 工具调用审批请求表（A 类 MCP 工具令牌重试）"""

    __tablename__ = 'hasn_agent_approval_requests'

    id: Mapped[id_key] = mapped_column(init=False)
    request_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='审批请求业务 ID（areq_{ulid}）')
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='发起调用的 Agent hasn_id')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='审批人（主人）hasn_id')
    tool_name: Mapped[str] = mapped_column(sa.String(128), default='', comment='被调用的工具 canonical name')
    args_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment='入参 canonical JSON 的 sha256（票据绑定，防换参重放）')
    args_digest: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='入参脱敏摘要 JSON（卡片展示用，不存敏感原文）')
    capability_keys: Mapped[list] = mapped_column(postgresql.JSONB(), default_factory=list, comment='触发 ask 的能力 key 列表（总是允许时据此写回 capability_modes=allow）')
    description: Mapped[str] = mapped_column(sa.String(500), default='', comment='人类可读的审批描述（NLG，卡片标题/正文）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='审批状态 (pending:待审:orange/approved:已批:green/denied:已拒:red/timeout:超时:gray/consumed:已用:blue)')
    grant_scope: Mapped[str | None] = mapped_column(sa.String(8), default=None, comment='授权粒度 (once:本次:blue/always:总是:green)')
    ticket_jti: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='签发的一次性票据 jti（防重放追踪）')
    decided_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='主人决定时间')
    expires_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='审批超时时间（默认 now+600s）')

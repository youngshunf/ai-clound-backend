from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key
from backend.utils.timezone import timezone


class HasnAgentRuntimeReports(Base):
    """HASN Agent Runtime 脱敏摘要上报表"""

    __tablename__ = 'hasn_agent_runtime_reports'

    id: Mapped[id_key] = mapped_column(init=False)
    report_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Runtime report 唯一 ID (rr_{uuid})')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Agent Owner hasn_id')
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Agent hasn_id')
    node_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='上报 Node ID')
    runtime_type: Mapped[str] = mapped_column(sa.String(30), default='', comment='Runtime 类型 (claude_code:Claude Code:purple/codex:Codex:blue/hermes:Hermes:green/webhook:Webhook:orange/cloud_sdk:Cloud SDK:cyan/none:无:gray)')
    runtime_status: Mapped[str] = mapped_column(sa.String(30), default='', comment='Runtime 状态 (online:在线:green/offline:离线:gray/unavailable:不可用:orange/error:错误:red/unknown:未知:gray)')
    adapter_registered: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='RuntimeAdapter 是否已注册')
    handle_available: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='RuntimeHandle 是否可调度')
    binding_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='公共 Binding ID 摘要（可空）')
    runtime_revision: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='Runtime 摘要修订号')
    summary_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='脱敏 Runtime Summary；禁止 workspace/endpoint/PID/CLI args/OAuth path')
    last_seen_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='Runtime 最后可见时间')
    reported_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='上报时间')

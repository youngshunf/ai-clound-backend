from datetime import datetime, date
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.external_mcp.model._base import ExternalMcpAppBase
from backend.common.model import id_key


class ExternalMcpUsage(ExternalMcpAppBase):
    """第三方MCP调用用量账本（平台key配额/计费归属）"""

    __tablename__ = 'external_mcp_usage'

    id: Mapped[id_key] = mapped_column(init=False)
    mcp_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='被调 server mcp_id')
    caller_owner_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='调用方主人 hasn_id（计费/配额归属）')
    caller_agent_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='调用方 Agent hasn_id')
    tool_name: Mapped[str] = mapped_column(sa.String(200), default='', comment='调用的 canonical 工具名（hasn.ext.{ns}.{tool}）')
    origin: Mapped[str] = mapped_column(sa.String(20), default='', comment='凭据归属（system=平台付费摊调用方/owner=用户自带）')
    trace_id: Mapped[str | None] = mapped_column(sa.String(80), default=None, comment='trace_id（贯穿发现/调用/审计）')
    success: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='调用是否成功')
    cost_units: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='计费单元（默认 1 次/单位）')
    day_bucket: Mapped[date] = mapped_column(sa.DATE(), default_factory=date.today, comment='UTC 自然日（per-owner 每日配额聚合键）')

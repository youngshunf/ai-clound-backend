from datetime import datetime
from typing import Any

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.external_mcp.model._base import ExternalMcpAppBase
from backend.common.model import TimeZone, id_key
from backend.utils.timezone import timezone


class ExternalMcpBinding(ExternalMcpAppBase):
    """Agent↔第三方MCP server绑定（gate2 授权工具）"""

    __tablename__ = 'external_mcp_binding'

    id: Mapped[id_key] = mapped_column(init=False)
    binding_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='binding 业务主键 mcb_{ulid}')
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='被授权 Agent hasn_id')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='授权主人 hasn_id（行级隔离 + 平台 key 用量归因键）')
    mcp_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='绑定的 server mcp_id（→external_mcp_server.mcp_id）')
    enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否启用（owner 可临时停用，停用即移出可调用发现集）')
    allowed_tools: Mapped[list[dict[str, Any]]] = mapped_column(postgresql.JSONB(), default_factory=list, comment='授权工具映射 jsonb（[{raw_name, tool_name}]；只有命中的工具可发现可调）')
    owner_authorized_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='主人授权时间')

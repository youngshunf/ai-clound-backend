from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key


class HasnAgentMcpKeys(Base):
    """Agent MCP 接入凭证（稳定可吊销 API Key，落库只存哈希）"""

    __tablename__ = 'hasn_agent_mcp_keys'

    id: Mapped[id_key] = mapped_column(init=False)
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='归属 Agent 的 HASN ID')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='主人 HASN ID')
    owner_user_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='主人 sys_user.id')
    key_prefix: Mapped[str] = mapped_column(sa.String(32), default='', comment='明文前缀（展示/审计用，不可反推完整 key）')
    key_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment='SHA-256(完整 key) 十六进制，查表入口，唯一索引')
    # scopes 列已退役（实施102 S0）：MCP key 不再承载 scope 集，授权只看三态
    # capability_modes。列由迁移 DROP，model 不再映射。
    node_id: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='设备绑定 node_id（空=不限设备；默认签发即绑当前 node）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (active:启用:green/revoked:已吊销:red)')
    expire_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='过期时间（空=不过期，生命周期靠吊销/轮换管理）')
    last_used_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最近使用时间（审计 / 可疑使用排查）')

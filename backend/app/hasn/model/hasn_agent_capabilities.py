import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, UniversalText, id_key


class HasnAgentCapabilities(Base):
    """HASN Agent 能力声明表"""

    __tablename__ = 'hasn_agent_capabilities'

    id: Mapped[id_key] = mapped_column(init=False)
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Agent 的 hasn_id')
    capability_id: Mapped[str] = mapped_column(sa.String(100), default='', comment='能力唯一标识')
    name: Mapped[str] = mapped_column(sa.String(100), default='', comment='能力名称')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='能力描述')
    input_schema: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='输入 JSON Schema')
    output_schema: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='输出 JSON Schema')
    requires_permission: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='所需权限（JSONB: relation_types/min_trust_level/scopes）')
    tags: Mapped[str | None] = mapped_column(sa.String(0), default=None, comment='能力标签')
    estimated_time_ms: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='预计耗时（毫秒）')
    idempotent: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否幂等')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (active:启用:green/disabled:已禁用:orange)')

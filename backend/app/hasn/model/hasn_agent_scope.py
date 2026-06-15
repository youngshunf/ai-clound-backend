import sqlalchemy as sa

from sqlalchemy.dialects.postgresql import JSONB

from backend.common.model import MappedBase, TimeZone
from backend.utils.timezone import timezone


class HasnAgentScope(MappedBase):
    """Agent 能力授权三态策略（能力 scope 真相列，每 Agent 一行）。

    出厂全 Allow + 逐能力覆盖（capability_modes：tool/scope → allow|ask|deny）。
    **实际读写由 `backend/common/security/agent_jwt.py` 裸 SQL 完成**（原子三态判定 +
    grant_always 写穿）；本模型仅用于 ORM 元数据登记（ADR-15 收编 R1：消除"无模型 →
    自动对账/alembic 误判孤儿"风险），**不改变既有裸 SQL 查询路径**。

    表结构以 `backend/sql/hasn/migrations/2026-05-13-create-hasn-agent-scopes.sql`
    及后续 `2026-06-01`/`2026-06-12` 迁移为准（曾删除 legacy scopes/post_needs_review 列）。
    只有 `updated_time`、无 `created_time`，故继承 MappedBase（不带 DateTimeMixin）而非 Base。
    """

    __tablename__ = 'hasn_agent_scopes'
    __table_args__ = {'comment': 'Agent 能力授权三态策略（出厂全 Allow + 逐能力覆盖）'}

    id = sa.Column(sa.BigInteger, primary_key=True, autoincrement=True, comment='主键 id')
    agent_hasn_id = sa.Column(sa.String, nullable=False, unique=True, comment='Agent 的 HASN ID（每 Agent 一行）')
    owner_hasn_id = sa.Column(sa.String, nullable=False, index=True, comment='Agent 主人的 HASN ID')
    default_mode = sa.Column(
        sa.String, nullable=False, server_default='allow', comment='默认模式 (allow/ask/deny)'
    )
    capability_modes = sa.Column(
        JSONB, nullable=False, server_default='{}', comment='逐能力覆盖：{tool|scope: allow|ask|deny}'
    )
    updated_time = sa.Column(
        TimeZone, nullable=False, default=timezone.now, onupdate=timezone.now, server_default=sa.func.now()
    )

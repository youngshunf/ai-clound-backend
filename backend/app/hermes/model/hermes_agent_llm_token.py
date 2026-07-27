from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key
from backend.utils.timezone import timezone


class HermesAgentLlmToken(Base):
    """Hermes Agent 级 LLM token 隔离记录"""

    __tablename__ = 'hermes_agent_llm_token'

    id: Mapped[id_key] = mapped_column(init=False)
    agent_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='Agent 业务 ID')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='唤星用户 ID')
    newapi_user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='new-api users.id')
    newapi_token_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='new-api tokens.id')
    token_key_prefix: Mapped[str] = mapped_column(sa.String(16), default='', comment='token 明文前 8 字符（脱敏展示与审计）')
    token_key_sha256: Mapped[str] = mapped_column(sa.String(64), default='', comment='token 明文 SHA256（反查匹配，不可逆）')
    model_allowlist: Mapped[list[str] | None] = mapped_column(postgresql.JSONB(), default=None, comment='平台模型白名单 JSON，留空 = 跟随 user 默认')
    rate_limit_rps: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='单 Agent QPS 限速，留空 = 跟随 user 默认')
    per_token_quota_remaining: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='可选：单 token 独立配额；留空 = 与 user.quota 共享')
    issued_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='签发时间')
    revoked_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='撤销时间，NULL 表示有效')
    runtime_node_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='Runtime 节点 ID（预留 §08）')

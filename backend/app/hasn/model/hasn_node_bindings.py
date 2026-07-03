from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key
from backend.utils.timezone import timezone


class HasnNodeBindings(Base):
    """HASN Node Owner Binding 租约表"""

    __tablename__ = 'hasn_node_bindings'

    id: Mapped[id_key] = mapped_column(init=False)
    binding_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='绑定唯一标识 (格式: ob_{uuid})')
    node_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='节点 ID (格式: n_{uuid_short})')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Owner 的 hasn_id (格式: h_xxx)')
    auth_profile: Mapped[str] = mapped_column(sa.String(30), default='', comment='认证模式 (bearer_token:平台令牌:blue/owner_api_key:Owner API Key:green/mtls_bound_token:mTLS绑定令牌:purple/dpop_token:DPoP令牌:cyan)')
    scopes: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='授权 scopes JSON')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (active:生效中:green/expired:已过期:orange/revoked:已吊销:red/removed:已解绑:gray)')
    bound_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='绑定时间')
    expires_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='过期时间')
    renewed_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最近续期时间')
    revoked_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='吊销时间')
    revoke_reason: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='吊销原因 (manual_revoke:手动吊销:orange/credential_rotated:凭据轮换:blue/policy_violation:策略违规:red)')
    last_used_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最后使用时间')

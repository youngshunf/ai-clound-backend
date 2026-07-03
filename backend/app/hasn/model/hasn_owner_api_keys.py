from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key


class HasnOwnerApiKeys(Base):
    """HASN Owner API Key 表"""

    __tablename__ = 'hasn_owner_api_keys'

    id: Mapped[id_key] = mapped_column(init=False)
    key_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Owner API Key 唯一标识')
    user_id: Mapped[int | None] = mapped_column(sa.BigInteger(), default=None, comment='平台用户 ID（桌面端/唤星账号场景）')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Owner 的 hasn_id (格式: h_xxx)')
    key_name: Mapped[str] = mapped_column(sa.String(100), default='', comment='Key 名称')
    key_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment='Owner API Key 的 SHA256 哈希')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='状态 (active:生效中:green/revoked:已吊销:red/deleted:已删除:gray)')
    scopes: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='授权 scopes JSON')
    bound_node_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='绑定 Node ID（可为空）')
    expires_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='过期时间')
    last_used_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最后使用时间')
    revoked_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='吊销时间')
    revoke_reason: Mapped[str | None] = mapped_column(sa.String(50), default=None, comment='吊销原因 (manual_revoke:手动吊销:orange/credential_rotated:凭据轮换:blue/policy_violation:策略违规:red)')

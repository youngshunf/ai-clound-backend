import uuid

from datetime import datetime
from uuid import UUID
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone
from backend.utils.timezone import timezone


class HasnNodeAuthorizationCodes(Base):
    """云端节点设备授权码"""

    __tablename__ = 'hasn_node_authorization_codes'

    id: Mapped[UUID] = mapped_column(sa.UUID(), primary_key=True, default_factory=uuid.uuid4, init=False, comment='主键 ID')
    code_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment='授权码 sha256 十六进制，明文不入库')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='平台用户 ID')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='主人 HASN ID')
    node_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='预分配的 hasn_nodes.node_id')
    purpose: Mapped[str] = mapped_column(sa.String(24), default='', comment='用途 (create:首次创建:blue/reauthorize:重新授权:orange)')
    expires_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='过期时刻（签发 + 5 分钟）')
    consumed_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='兑换时刻')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (pending:待兑换:blue/consumed:已兑换:green/expired:已过期:orange/revoked:已作废:red)')

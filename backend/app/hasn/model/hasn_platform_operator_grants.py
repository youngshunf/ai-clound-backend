import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnPlatformOperatorGrants(Base):
    """平台运维授予源（Admin-only·G1 特权门）"""

    __tablename__ = 'hasn_platform_operator_grants'

    id: Mapped[id_key] = mapped_column(init=False)
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='被授予的分身 hasn_id')
    scope: Mapped[str] = mapped_column(sa.String(64), default='', comment='特权 scope（精确值 diag:read:all / diag:manage 或段尾通配 ops:*，* 仅限末段）')
    granted_by: Mapped[str] = mapped_column(sa.String(64), default='', comment='操作的 Admin（审计）')
    note: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='备注（授予理由，可空）')

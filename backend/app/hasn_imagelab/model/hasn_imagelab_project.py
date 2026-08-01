import uuid

from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base


class HasnImagelabProject(Base):
    """历史图坊本地引用兼容登记。

    当前图坊直接使用平台项目 UUID；本表仅保留「历史 daemon 本地引用(local_ref)
    → 兼容 id(server_id)」的幂等映射，供旧客户端与旧深链过渡。
    """

    __tablename__ = 'hasn_imagelab_project'

    id: Mapped[UUID] = mapped_column(
        sa.UUID(), primary_key=True, default=uuid.uuid4, init=False, comment='历史兼容 server_id'
    )
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属主人 hasn_id（行级隔离键）')
    local_ref: Mapped[str] = mapped_column(
        sa.String(64), default='', comment='daemon 历史本地引用（仅作兼容映射与去重）'
    )
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment='历史显示名（供旧卡片展示）')

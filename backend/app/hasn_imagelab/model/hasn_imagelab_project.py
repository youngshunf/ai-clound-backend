import uuid

from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base


class HasnImagelabProject(Base):
    """图坊项目云端轻登记（云端权威 ID 源，模块 14 doc30 §5.9 B1）

    云端权威 ID 源：本表 id（UUID）即项目「云端权威 ID」（= server_id），供
    hasn://imagelab/projects/{id} URI 与云端派发/完成卡片深链使用。
    图坊 7 张业务表在 daemon 本地 SQLite（本地权威），云端不镜像业务数据；
    本表只做「daemon 本地项目(local_ref) → 云端权威 id(server_id)」的轻量映射（幂等 upsert）。
    """

    __tablename__ = 'hasn_imagelab_project'

    id: Mapped[UUID] = mapped_column(
        sa.UUID(), primary_key=True, default=uuid.uuid4, init=False, comment='云端权威 ID（server_id）'
    )
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属主人 hasn_id（行级隔离键）')
    local_ref: Mapped[str] = mapped_column(
        sa.String(64), default='', comment='daemon 本地项目 ULID（本地权威 ID，仅作映射/去重，映射只存 daemon 侧）'
    )
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment='项目名（供派发/完成卡片展示）')

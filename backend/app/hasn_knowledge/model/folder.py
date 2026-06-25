from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_knowledge.model._base import KnowledgeBase
from backend.common.model import TimeZone, id_key


class Folder(KnowledgeBase):
    """知识库目录树（纯组织元数据，RAGFlow 不感知）"""

    __tablename__ = 'folder'

    id: Mapped[id_key] = mapped_column(init=False)
    kb_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属知识库 ID（引用 hasn_knowledge.kb）')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属 owner HASN ID（owner 隔离键）')
    parent_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='父目录 ID（NULL=库根；任意层嵌套，自引用）')
    name: Mapped[str] = mapped_column(sa.String(128), default='', comment='目录名（同层同名拒绝）')
    sort_order: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='排序序号')
    deleted_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='软删除时间（删除仅限空目录）')

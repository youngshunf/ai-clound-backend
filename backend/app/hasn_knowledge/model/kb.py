from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_knowledge.model._base import KnowledgeBase
from backend.common.model import id_key, TimeZone
from backend.utils.timezone import timezone


class Kb(KnowledgeBase):
    """知识库（云端权威；RAGFlow dataset 为派生映射）"""

    __tablename__ = 'kb'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属 owner HASN ID（owner 隔离键，引用 public.hasn_humans）')
    scope: Mapped[str] = mapped_column(sa.String(16), default='', comment='工作区语义 (personal:个人:blue/enterprise:企业:purple)')
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='企业 ID（scope=enterprise 必填）')
    visibility: Mapped[str] = mapped_column(sa.String(16), default='private', comment='可见面 (private:私有:gray/enterprise:企业可见:blue/link:链接:cyan)')
    name: Mapped[str] = mapped_column(sa.String(128), default='', comment='库名')
    description: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='描述')
    ragflow_dataset_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='RAGFlow dataset 映射（唯一，派生物）')
    embedding_model: Mapped[str] = mapped_column(sa.String(128), default='', comment='向量模型（建库时固化，来自实例 config.default_embd_id）')
    document_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='文档数（反规范化计数，状态对账时回写）')
    chunk_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='分块数（反规范化计数，状态对账时回写）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (active:正常:green/deleting:删除中:orange)')
    deleted_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='软删除时间')

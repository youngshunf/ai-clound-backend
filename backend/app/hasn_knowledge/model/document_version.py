from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_knowledge.model._base import KnowledgeBase
from backend.common.model import id_key, UniversalText


class DocumentVersion(KnowledgeBase):
    """原生文档版本历史（只增不改）"""

    __tablename__ = 'document_version'

    id: Mapped[id_key] = mapped_column(init=False)
    document_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属文档 ID（引用 hasn_knowledge.document）')
    version_no: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='版本号（自 1 递增）')
    title: Mapped[str] = mapped_column(sa.String(255), default='', comment='该版本标题快照')
    content: Mapped[str] = mapped_column(UniversalText, default='', comment='该版本正文快照')
    source: Mapped[str] = mapped_column(sa.String(16), default='', comment='版本来源 (ui:用户:blue/agent:分身:purple)')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='产生该版本的分身 HASN ID（source=agent 时归因）')

from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_knowledge.model._base import KnowledgeBase
from backend.common.model import id_key, UniversalText, TimeZone
from backend.utils.timezone import timezone


class Document(KnowledgeBase):
    """知识库文档（元数据 + 原生正文；file 原件在平台私有桶）"""

    __tablename__ = 'document'

    id: Mapped[id_key] = mapped_column(init=False)
    kb_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属知识库 ID（引用 hasn_knowledge.kb）')
    folder_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='所属目录 ID（NULL=库根，引用 hasn_knowledge.folder）')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属 owner HASN ID（冗余，行级隔离热路径免 JOIN）')
    kind: Mapped[str] = mapped_column(sa.String(16), default='', comment='文档类型 (file:上传文件:blue/native:原生文档:green)')
    name: Mapped[str] = mapped_column(sa.String(255), default='', comment='文档名')
    size_bytes: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='大小字节数（native 取正文派生值）')
    mime_type: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='MIME 类型（native 恒 text/markdown）')
    content: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='原生文档 Markdown 正文（仅 native，PG 权威；file 恒 NULL）')
    asset_uri: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='原件私有桶引用 hasn://asset/{asset_id}（仅 file，权威存储 D10；native 恒 NULL）')
    current_version: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='当前版本号（仅 native，配 document_version）')
    ragflow_document_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='RAGFlow document 映射（派生物；同步成功后回填，native 重保存会更换）')
    parse_status: Mapped[str] = mapped_column(sa.String(16), default='', comment='索引状态 (uploading:上传中:gray/parsing:索引中:blue/parsed:已就绪:green/failed:索引失败:red)')
    parse_error: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='索引失败原因（如实落库，零 fake）')
    chunk_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='分块数')
    source: Mapped[str] = mapped_column(sa.String(16), default='', comment='创建来源 (ui:用户:blue/agent:分身:purple)')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='创建分身 HASN ID（source=agent 时归因）')
    deleted_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='软删除时间')

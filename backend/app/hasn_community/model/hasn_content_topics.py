from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnContentTopics(Base):
    """内容与话题关联表"""

    __tablename__ = 'hasn_content_topics'

    id: Mapped[id_key] = mapped_column(init=False)
    topic_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='关联话题 topic_id')
    content_type: Mapped[str] = mapped_column(sa.String(10), default='', comment='内容类型 (post:帖子/article:文章)')
    content_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='内容 ID（post_id 或 article_id）')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='内容责任主体 hasn_id，便于按主体过滤/治理')

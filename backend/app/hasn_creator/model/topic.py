import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_creator.model._base import HasnCreatorAppBase
from backend.common.model import UniversalText, id_key


class Topic(HasnCreatorAppBase):
    """选题池；按画像 + 热点 + 竞品推荐选题 + 采纳/跳过"""

    __tablename__ = 'topic'

    id: Mapped[id_key] = mapped_column(init=False)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    title: Mapped[str] = mapped_column(sa.String(200), default='', comment=None)
    potential_score: Mapped[float] = mapped_column(sa.REAL(), default=0.0, comment=None)
    heat_index: Mapped[float] = mapped_column(sa.REAL(), default=0.0, comment=None)
    reason: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    keywords: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    creative_angles: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    status: Mapped[int] = mapped_column(sa.SMALLINT(), default=0, comment='状态 (0:待处理:gray/1:已采纳:green/2:已跳过:red)')
    content_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='采纳后关联内容（content.id 逻辑引用）')
    batch_date: Mapped[str | None] = mapped_column(sa.String(20), default=None, comment=None)
    source_uid: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment=None)

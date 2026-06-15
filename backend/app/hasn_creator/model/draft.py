from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText
from backend.app.hasn_creator.model._base import HasnCreatorAppBase


class Draft(HasnCreatorAppBase):
    """草稿箱（灵感快速捕获，轻量独立于正式流水线）"""

    __tablename__ = 'draft'

    id: Mapped[id_key] = mapped_column(init=False)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    title: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment=None)
    content: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    media: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='媒体引用（hasn://asset/）')
    tags: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    target_platforms: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)

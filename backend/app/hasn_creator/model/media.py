import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_creator.model._base import HasnCreatorAppBase
from backend.common.model import UniversalText, id_key


class Media(HasnCreatorAppBase):
    """素材库；配图/封面/视频/模板（私有桶引用）"""

    __tablename__ = 'media'

    id: Mapped[id_key] = mapped_column(init=False)
    project_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment=None)
    owner_scope: Mapped[str] = mapped_column(sa.String(16), default='', comment=None)
    enterprise_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    assignee: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    type: Mapped[str] = mapped_column(sa.String(20), default='', comment='类型 (image:图片:blue/video:视频:purple/audio:音频:orange/template:模板:green)')
    asset_uri: Mapped[str] = mapped_column(UniversalText, default='', comment='私有桶引用（hasn://asset/）')
    filename: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment=None)
    file_size: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment=None)
    width: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment=None)
    height: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment=None)
    duration: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment=None)
    thumbnail_uri: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    tags: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)

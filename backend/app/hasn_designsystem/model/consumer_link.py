import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_designsystem.model._base import DesignSystemBase
from backend.common.model import id_key


class ConsumerLink(DesignSystemBase):
    """设计系统下游消费登记（换系统重渲染追踪）"""

    __tablename__ = 'consumer_link'

    id: Mapped[id_key] = mapped_column(init=False)
    design_system_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='所属 design_system.id')
    consumer_app: Mapped[str] = mapped_column(sa.String(32), default='', comment='消费方 (deck:演示文稿:violet/publish:网站发布:blue/creator:创作:cyan)')
    consumer_ref: Mapped[str] = mapped_column(sa.String(128), default='', comment='消费方资源 id（如 deck_id）')
    bound_revision_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='绑定的 revision.id（消费的具体版本，可空）')

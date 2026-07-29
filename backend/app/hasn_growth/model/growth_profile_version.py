from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_growth.model._base import HasnGrowthAppBase
from backend.common.model import id_key


class GrowthProfileVersion(HasnGrowthAppBase):
    """获客项目已确认画像的不可变版本历史"""

    __tablename__ = 'growth_profile_version'

    id: Mapped[id_key] = mapped_column(init=False)
    growth_project_id: Mapped[str | UUID] = mapped_column(sa.UUID(), default=None, comment=None)
    version: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment=None)
    product_profile: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    icp_profile: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment=None)
    knowledge_document_versions: Mapped[list[dict]] = mapped_column(
        postgresql.JSONB(),
        default_factory=list,
        comment='参与画像确认的 Knowledge 文档及版本 [{document_id,version}]',
    )
    source_hash: Mapped[str] = mapped_column(sa.String(64), default='', comment='参与文档稳定 ID 与版本的规范化 SHA256')
    confirmed_by_kind: Mapped[str] = mapped_column(
        sa.String(16),
        default='',
        comment='确认主体 (owner:主人:blue/migration:迁移:gray)',
    )
    confirmed_by_id: Mapped[str] = mapped_column(sa.String(64), default='', comment=None)

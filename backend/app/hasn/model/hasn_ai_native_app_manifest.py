from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key


class HasnAiNativeAppManifest(Base):
    """HASN AI-Native 应用清单表"""

    __tablename__ = 'hasn_ai_native_app_manifest'

    id: Mapped[id_key] = mapped_column(init=False)
    app_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='应用 ID')
    version: Mapped[str] = mapped_column(sa.String(40), default='', comment='版本号')
    status: Mapped[str] = mapped_column(
        sa.String(16), default='draft', comment='状态 (draft:草稿:gray/published:已发布:green)'
    )
    workspace_scope: Mapped[list] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment='支持的 workspace 范围'
    )
    collaboration_mode: Mapped[str] = mapped_column(
        sa.String(24), default='none', comment='协作模式 (none:无:gray/workspace_shared:工作空间共享:green)'
    )
    manifest_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='Manifest JSON')
    manifest_hash: Mapped[str] = mapped_column(sa.String(128), default='', comment='Manifest hash')
    published_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='发布时间')

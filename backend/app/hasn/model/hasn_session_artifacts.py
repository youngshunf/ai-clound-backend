import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, UniversalText, id_key


class HasnSessionArtifacts(Base):
    """HASN 会话产物表"""

    __tablename__ = 'hasn_session_artifacts'

    id: Mapped[id_key] = mapped_column(init=False)
    session_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='会话 ID')
    artifact_kind: Mapped[str] = mapped_column(sa.String(50), default='', comment='产物类型 (file/code/report/data)')
    artifact_name: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='产物名称')
    artifact_path: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='产物路径')
    summary_json: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='产物摘要 (JSON)')
    sync_policy: Mapped[str] = mapped_column(sa.String(20), default='', comment='同步策略 (full/metadata_only/local_only)')

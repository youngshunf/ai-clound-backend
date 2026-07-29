from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, UniversalText, TimeZone
from backend.utils.timezone import timezone


class HasnStorageExportItems(Base):
    """用户云存储导出逐资产不可变快照"""

    __tablename__ = 'hasn_storage_export_items'

    id: Mapped[id_key] = mapped_column(init=False)
    item_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='导出明细稳定 ID')
    job_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='所属导出作业 ID')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='所属主人 hasn_id')
    asset_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='快照中的逻辑资产 ID')
    logical_path: Mapped[str] = mapped_column(UniversalText, default='', comment='创建导出时冻结的逻辑路径')
    original_name: Mapped[str] = mapped_column(sa.String(500), default='', comment='创建导出时冻结的原始文件名')
    mime: Mapped[str] = mapped_column(sa.String(200), default='', comment='MIME 类型')
    source_app: Mapped[str | None] = mapped_column(sa.String(80), default=None, comment='业务来源应用')
    access: Mapped[str] = mapped_column(sa.String(16), default='', comment='对象访问级别')
    asset_created_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='资产原始创建时间')
    lifecycle_status: Mapped[str] = mapped_column(sa.String(20), default='', comment='创建导出时的生命周期状态')
    bindings: Mapped[list[dict[str, str]]] = mapped_column(
        postgresql.JSONB(),
        default_factory=list,
        comment='创建导出时冻结的业务引用清单',
    )
    object_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='物理对象 ID')
    storage_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='快照中的存储配置 ID')
    object_key: Mapped[str] = mapped_column(sa.String(1024), default='', comment='快照中的对象键')
    size_bytes: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='预期对象字节数')
    sha256: Mapped[str | None] = mapped_column(sa.CHAR(), default=None, comment='预期对象 SHA-256')
    verify_status: Mapped[str] = mapped_column(sa.String(24), default='', comment='校验状态 (pending:待校验:blue/verified:已校验:green/failed:失败:red)')
    error_code: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='失败错误码')

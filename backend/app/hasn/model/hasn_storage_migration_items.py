from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnStorageMigrationItems(Base):
    """用户云存储迁移逐对象明细"""

    __tablename__ = 'hasn_storage_migration_items'

    id: Mapped[id_key] = mapped_column(init=False)
    item_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='迁移明细稳定 ID')
    job_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='所属迁移作业 ID')
    object_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='被迁移物理对象 ID')
    source_storage_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='迁移前存储配置 ID')
    source_object_key: Mapped[str] = mapped_column(sa.String(1024), default='', comment='迁移前对象键')
    source_key_layout: Mapped[str] = mapped_column(sa.String(24), default='', comment='迁移前对象键布局（精确回滚依据）')
    target_storage_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='目标存储配置 ID')
    target_object_key: Mapped[str] = mapped_column(sa.String(1024), default='', comment='目标对象键')
    source_size_bytes: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='源对象校验字节数')
    source_sha256: Mapped[str | None] = mapped_column(sa.CHAR(), default=None, comment='源对象校验 SHA-256')
    verify_status: Mapped[str] = mapped_column(sa.String(24), default='', comment='校验状态 (pending:待处理:blue/copied:已复制:orange/verified:已校验:green/switched:已切换:green/rolled_back:已回滚:gray/failed:失败:red)')
    source_cleanup_status: Mapped[str] = mapped_column(sa.String(24), default='retained', comment='源对象清理状态 (retained:观察期保留:blue/deleted:已清理:green/shared:跨主人共享保留:orange/failed:清理失败:red)')
    source_deleted_time: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None, comment='源对象确认删除时间')
    error_code: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='失败错误码')

from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class HasnStorageJobs(Base):
    """用户云存储持久作业与补偿 outbox"""

    __tablename__ = 'hasn_storage_jobs'

    id: Mapped[id_key] = mapped_column(init=False)
    job_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='作业稳定 ID')
    owner_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='所属主人 hasn_id；平台对账可为空')
    job_type: Mapped[str] = mapped_column(sa.String(32), default='', comment='作业类型 (storage_export:导出:blue/storage_migration:迁移:orange/storage_reconcile:对账:green/object_purge:对象清理:red/orphan_cleanup:孤儿清理:orange/unbound_asset_sweep:无引用资产清扫:blue/multipart_abort_sweep:分片清理:purple)')
    status: Mapped[str] = mapped_column(sa.String(24), default='', comment='作业状态 (pending:待执行:blue/running:执行中:orange/retrying:重试中:orange/paused:已暂停:gray/succeeded:成功:green/failed:失败:red/cancelled:已取消:gray)')
    cursor: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='数据库权威游标')
    total_items: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='总条目数')
    processed_items: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='已处理条目数')
    failed_items: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='失败条目数')
    error_code: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='稳定错误码')
    payload: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='作业输入与补偿数据')
    result: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='作业结果摘要')
    attempt_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='已执行次数')
    next_attempt_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='下次重试时间')
    expires_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='作业或产物过期时间')

from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, UniversalText, TimeZone
from backend.utils.timezone import timezone


class HasnArtifactRegistrationOutbox(Base):
    """Agent 产物登记可靠投递与修复队列"""

    __tablename__ = 'hasn_artifact_registration_outbox'

    id: Mapped[id_key] = mapped_column(init=False)
    outbox_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='队列记录公开标识')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='主人隔离键')
    artifact_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='已归一产物公开标识')
    idempotency_key: Mapped[str] = mapped_column(sa.String(768), default='', comment='登记来源幂等键')
    payload: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='不含正文和本地绝对路径的修复载荷')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='投递状态 (pending:待处理:processing:处理中:completed:已完成:dead_letter:终局失败)')
    attempt_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='已尝试次数')
    next_retry_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='下次可领取时间')
    lease_until: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='处理租约截止时间')
    last_error: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='最近失败诊断')

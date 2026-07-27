from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, UniversalText, TimeZone
from backend.utils.timezone import timezone


class HasnRelationCommandOutbox(Base):
    """身份事实投影为 IM 关系的可靠命令队列"""

    __tablename__ = 'hasn_relation_command_outbox'

    id: Mapped[id_key] = mapped_column(init=False)
    command_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='命令公开标识')
    command_type: Mapped[str] = mapped_column(sa.String(64), default='', comment='关系命令类型')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='控制边主人 HASN ID')
    peer_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='主人名下分身 HASN ID')
    idempotency_key: Mapped[str] = mapped_column(sa.String(160), default='', comment='跨重试稳定幂等键')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='投递状态：pending/processing/completed/dead_letter')
    attempt_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='已失败次数')
    next_retry_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='下次允许领取时间')
    lease_until: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='处理租约截止时间')
    last_error: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='最近一次失败诊断')
    completed_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='投递完成时间')

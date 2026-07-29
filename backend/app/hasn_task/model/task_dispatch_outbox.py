from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_task.model._base import HasnTaskAppBase
from backend.common.model import id_key, UniversalText, TimeZone
from backend.utils.timezone import timezone


class TaskDispatchOutbox(HasnTaskAppBase):
    """中心任务调度器向主人节点可靠投递任务执行帧的事务队列"""

    __tablename__ = 'task_dispatch_outbox'

    id: Mapped[id_key] = mapped_column(init=False)
    command_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='派发命令公开标识')
    run_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='同事务创建的任务运行 ID，同时作为单次派发唯一键')
    task_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='任务定义 ID')
    target_owner_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='接收任务执行帧的主人 HASN ID')
    method: Mapped[str] = mapped_column(sa.String(64), default='', comment='HASN 协议方法，固定 hasn.task.exec')
    payload: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='完整任务执行参数 JSON，不包含 HASN 外层信封')
    payload_hash: Mapped[str] = mapped_column(sa.CHAR(), default='', comment='规范化目标、方法与载荷的 SHA-256')
    idempotency_key: Mapped[str] = mapped_column(sa.String(160), default='', comment='由权威 run ID 派生的稳定派发幂等键')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='投递状态：pending/processing/completed/dead_letter')
    attempt_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='已失败次数')
    next_attempt_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='下次允许领取时间')
    lease_until: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='处理中租约截止时间')
    locked_by: Mapped[str | None] = mapped_column(sa.String(160), default=None, comment='当前 relay 实例标识')
    last_error: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='最近一次失败诊断')
    completed_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='已交给实时投递层的时间')

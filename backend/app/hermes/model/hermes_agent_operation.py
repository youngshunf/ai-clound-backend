from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, id_key
from backend.utils.timezone import timezone


class HermesAgentOperation(Base):
    """Hermes Agent 操作记录表"""

    __tablename__ = 'hermes_agent_operation'

    id: Mapped[id_key] = mapped_column(init=False)
    operation_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='操作 ID')
    agent_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='Agent 业务 ID')
    user_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='用户 ID')
    operation_type: Mapped[str] = mapped_column(sa.String(32), default='', comment='操作类型 (create_agent:创建:blue/update_agent:更新:blue/delete_agent:删除:red/start_gateway:启动:green/restart_gateway:重启:orange/stop_gateway:停止:gray/bind_channel:绑定:purple/unbind_channel:解绑:orange/chat:对话:green/run:运行:cyan/sync_runtime:同步:blue)')
    operation_status: Mapped[str] = mapped_column(sa.String(20), default='', comment='操作状态 (started:已开始:blue/succeeded:成功:green/failed:失败:red/cancelled:已取消:gray)')
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='幂等键')
    runtime_request_id: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='Runtime 请求 ID')
    started_at: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='开始时间')
    finished_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='结束时间')
    request_summary_json: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='脱敏请求摘要 JSON')
    response_summary_json: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='脱敏响应摘要 JSON')
    error_json: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='错误 JSON')

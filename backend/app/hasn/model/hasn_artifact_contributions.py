from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key, TimeZone
from backend.utils.timezone import timezone


class HasnArtifactContributions(Base):
    """Agent 对产物的不可变参与记录"""

    __tablename__ = 'hasn_artifact_contributions'

    id: Mapped[id_key] = mapped_column(init=False)
    contribution_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='参与记录公开标识')
    artifact_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='关联产物当前态公开标识')
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='主人隔离键')
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='参与分身标识')
    work_session_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='本次参与所属工作会话')
    project_id: Mapped[str | UUID | None] = mapped_column(sa.UUID(), default=None, comment='本次参与所属平台项目')
    action: Mapped[str] = mapped_column(sa.String(16), default='', comment='参与动作 (create:新增:update:修改)')
    source_kind: Mapped[str] = mapped_column(sa.String(32), default='', comment='参与来源 (app_write:应用写入:platform_tool:平台工具:runtime_file:运行时文件:agent_note:分身自撰:external_import:外部导入)')
    source_tool: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='实际写工具或处理器名称')
    source_app_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='本次操作所在应用上下文')
    dispatch_id: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='派发关联标识')
    tool_call_id: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='工具调用标识')
    source_event_id: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='来源事件标识')
    idempotency_key: Mapped[str] = mapped_column(sa.String(768), default='', comment='来源幂等键')
    conversation_id: Mapped[str | UUID | None] = mapped_column(sa.UUID(), default=None, comment='来源会话标识')
    message_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='来源消息标识')
    occurred_time: Mapped[datetime] = mapped_column(TimeZone, default_factory=timezone.now, comment='真实写入或后置核验完成时间')
    meta_data: Mapped[dict] = mapped_column('metadata',postgresql.JSONB(), default_factory=dict, comment='不含正文和本地绝对路径的上下文快照')

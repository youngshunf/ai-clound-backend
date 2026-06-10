"""云端任务运行摘要模型（hasn_task.run_summary，原 public.hasn_task_run_summary）。"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_task.model._base import HasnTaskAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class HasnTaskRunSummary(HasnTaskAppBase):
    """v2.1 云端任务运行摘要表"""

    __tablename__ = 'run_summary'

    id: Mapped[id_key] = mapped_column(init=False)
    run_uuid: Mapped[str] = mapped_column(sa.String(64), default='', unique=True, comment='端云稳定运行 UUID')
    task_uuid: Mapped[str] = mapped_column(sa.String(64), default='', comment='端云稳定任务 UUID')
    owner_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='任务归属 owner')
    agent_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='执行 Agent HASN ID')
    executor_node_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='执行节点 ID')
    session_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='任务执行 session ID')
    scheduled_fire_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='计划触发时间')
    dedupe_key: Mapped[str] = mapped_column(sa.String(200), default='', unique=True, comment='运行摘要幂等键')
    status: Mapped[str] = mapped_column(sa.String(20), default='', comment='运行状态')
    output_summary: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='输出摘要')
    error: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='错误摘要')
    deep_link: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='投影 deep link')
    model: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='执行模型')
    token_usage: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='Token 消耗摘要')
    duration_ms: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='执行耗时（毫秒）')
    started_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='开始时间')
    finished_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='完成时间')
    continued_from_run_id: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='本次 run 接续自哪条 run（端云稳定 run UUID，与 hasn_task.run 同名列，D2）'
    )
    artifacts_json: Mapped[list[dict] | None] = mapped_column(
        postgresql.JSONB(), default=None,
        comment=(
            '产物引用清单 [{kind, filename, local_path?, producer_node_id, asset_id?, asset_uri?, '
            'available}]（D6，跨设备可见）'
        )
    )

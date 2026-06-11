"""任务执行记录模型（hasn_task.run，原 public.hasn_task_run）。"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_task.model._base import HasnTaskAppBase
from backend.common.model import TimeZone, UniversalText, id_key

TASK_RUN_STATUS_COMMENT = (
    '执行状态 (pending:待执行:blue/running:执行中:orange/success:成功:green/'
    'error:失败:red/timeout:超时:orange/silent:静默:gray)'
)
PROMPT_SNAPSHOT_COMMENT = '执行时的完整 prompt（包含加载的 skill bundle 和 skill 内容）'
TOKEN_USAGE_COMMENT = 'Token 消耗 {input_tokens, output_tokens, total_tokens}'


class HasnTaskRun(HasnTaskAppBase):
    """任务执行记录表"""

    __tablename__ = 'run'

    id: Mapped[id_key] = mapped_column(init=False)
    task_id: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='关联任务 ID')
    agent_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='执行 agent ID')
    session_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='任务执行逻辑 session ID')
    source_conversation_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='任务来源会话 ID')
    source_message_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='任务来源消息 ID')
    runtime_node_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='执行的 hasn-node 节点 ID')
    status: Mapped[str] = mapped_column(
        sa.String(20),
        default='',
        comment=TASK_RUN_STATUS_COMMENT,
    )
    started_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='开始执行时间')
    finished_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='完成时间')
    duration_ms: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='执行耗时（毫秒）')
    prompt_snapshot: Mapped[str | None] = mapped_column(
        UniversalText,
        default=None,
        comment=PROMPT_SNAPSHOT_COMMENT,
    )
    output: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='Agent 最终输出')
    error: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='错误信息')
    model: Mapped[str | None] = mapped_column(sa.String(100), default=None, comment='使用的模型')
    token_usage: Mapped[dict | None] = mapped_column(
        postgresql.JSONB(),
        default=None,
        comment=TOKEN_USAGE_COMMENT,
    )
    continued_from_run_id: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='本次 run 接续自哪条 run（端云稳定 run UUID，接续链 D2）'
    )
    workflow_run_uuid: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='所属工作流执行实例稳定 UUID（NULL=独立任务 run）'
    )
    node_key: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='本次执行对应的图内节点标识'
    )

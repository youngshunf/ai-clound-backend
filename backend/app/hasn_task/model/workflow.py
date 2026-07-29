"""工作流（任务图）定义模型（hasn_task.workflow）。

多任务编排：一个大任务分解成 N 个子任务节点 + 依赖边。节点复用 v3.0 的
hasn_task.task（加 workflow_uuid + node_key）。整图可定时周期跑（W4）。

设计事实源：docs/hasn-node设计文档/12-任务系统实施方案/07-多任务编排（工作流）设计.md §4.1。
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_task.model._base import HasnTaskAppBase
from backend.common.model import TimeZone, UniversalText, id_key

WORKFLOW_STATUS_COMMENT = (
    '状态 (draft:草稿:gray/active:启用:green/paused:已暂停:orange/archived:已归档:gray/'
    'pending_approval:待审批:orange/rejected:已拒绝:gray)'
)
WORKFLOW_CREATED_BY_KIND_COMMENT = '创建者类别 (owner:主人:blue/agent:分身:violet/builtin:内置:gray)'


class HasnWorkflow(HasnTaskAppBase):
    """工作流（任务图）定义表"""

    __tablename__ = 'workflow'

    id: Mapped[id_key] = mapped_column(init=False)
    workflow_uuid: Mapped[str] = mapped_column(
        sa.String(64), default='', unique=True, comment='端云稳定工作流 UUID（同步主键）'
    )
    owner_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='工作流归属 owner')
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment='工作流名称')
    template_key: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='实例溯源的工作流模板键；手工编排为 NULL'
    )
    instantiation_idempotency_key: Mapped[str | None] = mapped_column(
        sa.String(128), default=None, comment='Owner 场景实例化幂等键；同一 owner 重放返回同一工作流'
    )
    goal: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='总目标（也作整图验收口径）')
    schedule_type: Mapped[str] = mapped_column(
        sa.String(20), default='once', comment='整图定时 (once:一次性:blue/interval:间隔:green/cron:定时:orange)'
    )
    schedule_config: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='调度配置 JSON')
    schedule_display: Mapped[str | None] = mapped_column(sa.String(200), default=None, comment='人类可读调度描述')
    timezone: Mapped[str] = mapped_column(sa.String(64), default='Asia/Shanghai', comment='时区')
    misfire_policy: Mapped[str] = mapped_column(sa.String(20), default='run_once', comment='错过补跑策略')
    catchup_limit: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='补偿执行上限')
    enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否启用')
    status: Mapped[str] = mapped_column(sa.String(20), default='active', comment=WORKFLOW_STATUS_COMMENT)
    source: Mapped[str] = mapped_column(sa.String(32), default='owner', comment='来源 owner/agent')
    created_by_kind: Mapped[str] = mapped_column(
        sa.String(16), default='owner', comment=WORKFLOW_CREATED_BY_KIND_COMMENT
    )
    continuation_enabled: Mapped[bool] = mapped_column(
        sa.BOOLEAN(), default=False, comment='跨 fire 接续：上次整图产出注入下次入口节点（二期）'
    )
    next_run_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='整图下次触发时间')
    last_run_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='整图上次触发时间')
    workflow_revision: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='工作流定义服务端修订号')
    # 平台项目联邦挂靠（doc38·实施95 P9-A）：可空——裸工程图允许为空；场景实例化路径业务层硬闸必填。
    # 项目不是权限边界/不拥有执行语义，仅「为了哪件事」的业务归属标签，删项目=产物散落回各应用不中断执行。
    project_id: Mapped[UUID | None] = mapped_column(
        sa.UUID(), default=None, comment='所属平台项目 id（hasn_project.id，可空；场景实例化必填、裸工程图为空）'
    )
    deleted_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='删除时间')

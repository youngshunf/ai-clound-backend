"""任务定义模型（hasn_task.task，原 public.hasn_task）。"""

from datetime import datetime

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_task.model._base import HasnTaskAppBase
from backend.common.model import TimeZone, UniversalText, id_key

TASK_STATE_COMMENT = (
    '状态 (scheduled:已调度:blue/paused:已暂停:orange/completed:已完成:green/error:异常:red/'
    'deleted:已删除:gray/waiting_for_runtime:等待 Runtime:orange/needs_package_resolution:待解析包:orange/'
    'needs_skill_install:待安装技能:orange/pending_approval:待审批:orange/rejected:已拒绝:gray)'
)
CREATED_BY_KIND_COMMENT = '创建者类别 (owner:主人:blue/agent:分身:violet/builtin:内置:gray)'


class HasnTask(HasnTaskAppBase):
    """任务定义表"""

    __tablename__ = 'task'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='任务归属 owner')
    agent_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='执行 agent（必须有绑定 runtime）')
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment='任务名称')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='任务描述')
    prompt: Mapped[str] = mapped_column(UniversalText, default='', comment='任务指令（支持模板变量）')
    system_prompt: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='系统提示词')
    input_template: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='输入模板')
    skill_bundle_ids: Mapped[list[str]] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment='Skill bundle 名称列表，如 ["backend-dev", "mlops"]'
    )
    skill_bundle_refs: Mapped[list[dict]] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment='v2.1 marketplace skill pack 引用'
    )
    skill_ids: Mapped[list[str]] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment='单独 skill 名称列表，如 ["github-pr", "pytest"]'
    )
    skill_refs: Mapped[list[dict]] = mapped_column(postgresql.JSONB(), default_factory=list, comment='v2.1 skill 引用')
    workflow_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='工作流 ID（可选，未来扩展）')
    workflow: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='v2.1 workflow 投影')
    enabled_toolsets: Mapped[list[str] | None] = mapped_column(
        postgresql.JSONB(), default=None, comment='限制工具集 ["terminal", "file", "web"]（NULL=全部）'
    )
    context_from_task_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='链式任务：注入上次执行结果'
    )
    schedule_type: Mapped[str] = mapped_column(
        sa.String(20), default='', comment='调度类型 (once:一次性:blue/interval:间隔:green/cron:定时:orange)'
    )
    schedule_config: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict,
        comment='调度配置 JSON: {expr: "0 9 * * *"} 或 {minutes: 60} 或 {run_at: "2026-05-23T09:00:00Z"}'
    )
    schedule_display: Mapped[str | None] = mapped_column(
        sa.String(200), default=None, comment='人类可读调度描述，如"每天 9:00"'
    )
    timezone: Mapped[str] = mapped_column(sa.String(64), default='Asia/Shanghai', comment='任务时区')
    misfire_policy: Mapped[str] = mapped_column(sa.String(20), default='skip', comment='错过触发策略')
    catchup_limit: Mapped[int | None] = mapped_column(sa.INTEGER(), default=None, comment='补偿执行上限')
    enabled: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否启用')
    state: Mapped[str] = mapped_column(sa.String(40), default='', comment=TASK_STATE_COMMENT)
    next_run_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='下次执行时间')
    last_run_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='上次执行时间')
    last_status: Mapped[str | None] = mapped_column(
        sa.String(20),
        default=None,
        comment='上次执行状态 (ok:成功:green/error:错误:red/silent:静默:gray/timeout:超时:orange)',
    )
    last_error: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='上次错误信息')
    run_count: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='总执行次数')
    repeat_times: Mapped[int | None] = mapped_column(
        sa.INTEGER(),
        default=None,
        comment='重复次数（NULL=永久，N=执行N次）',
    )
    repeat_completed: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='已重复执行次数')
    created_by: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='创建者')
    task_uuid: Mapped[str | None] = mapped_column(sa.String(64), default=None, unique=True, comment='端云稳定任务 UUID')
    executor_policy: Mapped[str] = mapped_column(
        sa.String(32), default='local_node', comment='执行策略 local_node/cloud_runtime_host/unresolved'
    )
    executor_node_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='指定执行节点 ID')
    task_revision: Mapped[int] = mapped_column(sa.BIGINT(), default=0, comment='任务定义服务端修订号')
    deleted_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='删除时间')
    continuation_enabled: Mapped[bool] = mapped_column(
        sa.BOOLEAN(), default=False, comment='任务接续：把上次成功 run 的结果注入下次 session（D2）'
    )
    enable_subagents: Mapped[bool] = mapped_column(
        sa.BOOLEAN(), default=False, comment='允许任务会话内使用子分身 delegate_task（D5）'
    )
    created_by_kind: Mapped[str] = mapped_column(sa.String(16), default='owner', comment=CREATED_BY_KIND_COMMENT)

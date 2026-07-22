"""任务定义 Pydantic 模型（hasn_task 应用）。"""

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase

TASK_STATE_DESCRIPTION = (
    '状态 (scheduled:已调度:blue/paused:已暂停:orange/completed:已完成:green/error:异常:red/'
    'deleted:已删除:gray/waiting_for_runtime:等待 Runtime:orange/needs_package_resolution:待解析包:orange/'
    'needs_skill_install:待安装技能:orange/pending_approval:待审批:orange/rejected:已拒绝:gray)'
)


class HasnTaskSchemaBase(SchemaBase):
    """任务定义基础模型"""

    owner_id: str = Field(description='任务归属 owner')
    agent_id: str = Field(description='执行 agent（必须有绑定 runtime）')
    name: str = Field(description='任务名称')
    description: str | None = Field(None, description='任务描述')
    prompt: str = Field(description='任务指令（支持模板变量）')
    system_prompt: str | None = Field(None, description='系统提示词')
    input_template: str | None = Field(None, description='输入模板')
    skill_bundle_ids: list[str] = Field(
        default_factory=list, description='Skill bundle 名称列表，如 ["backend-dev", "mlops"]'
    )
    skill_bundle_refs: list[dict] = Field(default_factory=list, description='v2.1 marketplace skill pack 引用')
    skill_ids: list[str] = Field(default_factory=list, description='单独 skill 名称列表，如 ["github-pr", "pytest"]')
    skill_refs: list[dict] = Field(default_factory=list, description='v2.1 skill 引用')
    workflow_id: int | None = Field(None, description='工作流 ID（可选，未来扩展）')
    workflow: dict = Field(default_factory=dict, description='v2.1 workflow 投影')
    enabled_toolsets: list[str] | None = Field(None, description='限制工具集 ["terminal", "file", "web"]（NULL=全部）')
    context_from_task_id: int | None = Field(None, description='链式任务：注入上次执行结果')
    schedule_type: str = Field(description='调度类型 (once:一次性:blue/interval:间隔:green/cron:定时:orange)')
    schedule_config: dict = Field(
        description='调度配置 JSON: {expr: "0 9 * * *"} 或 {minutes: 60} 或 {run_at: "2026-05-23T09:00:00Z"}'
    )
    schedule_display: str | None = Field(None, description='人类可读调度描述，如"每天 9:00"')
    timezone: str = Field('Asia/Shanghai', description='任务时区')
    misfire_policy: str = Field('skip', description='错过触发策略')
    catchup_limit: int | None = Field(None, description='补偿执行上限')
    enabled: bool = Field(True, description='是否启用')
    state: str = Field('scheduled', description=TASK_STATE_DESCRIPTION)
    next_run_at: datetime | None = Field(None, description='下次执行时间')
    last_run_at: datetime | None = Field(None, description='上次执行时间')
    last_status: str | None = Field(
        None, description='上次执行状态 (ok:成功:green/error:错误:red/silent:静默:gray/timeout:超时:orange)'
    )
    last_error: str | None = Field(None, description='上次错误信息')
    run_count: int = Field(0, description='总执行次数')
    repeat_times: int | None = Field(None, description='重复次数（NULL=永久，N=执行N次）')
    repeat_completed: int = Field(0, description='已重复执行次数')
    created_time: datetime | None = Field(None, description='创建时间')
    updated_time: datetime | None = Field(None, description='更新时间')
    created_by: str | None = Field(None, description='创建者')
    task_uuid: str | None = Field(None, description='端云稳定任务 UUID')
    executor_policy: str = Field('local_node', description='执行策略 local_node/cloud_runtime_host/unresolved')
    executor_node_id: str | None = Field(None, description='指定执行节点 ID')
    task_revision: int = Field(0, description='任务定义服务端修订号')
    deleted_at: datetime | None = Field(None, description='删除时间')
    continuation_enabled: bool = Field(False, description='任务接续：把上次成功 run 的结果注入下次 session（D2）')
    enable_subagents: bool = Field(False, description='允许任务会话内使用子分身 delegate_task（D5）')
    created_by_kind: str = Field('owner', description='创建者类别 owner/agent/builtin')
    builtin_key: str | None = Field(None, description='内置任务全局唯一键（仅内置任务非空）')
    builtin_synced_revision: int | None = Field(
        None, description='播种/更新时同步的 catalog revision（可更新检测基准）'
    )
    project_id: str | UUID | None = Field(None, description='归属平台项目云端权威 id')
    app_id: str | None = Field(None, description='驱动应用 app_id')
    execution_kind: str = Field('freeform', description='执行方式 app_workflow/freeform')
    execution_spec: dict = Field(default_factory=dict, description='应用工作流或自由指令执行规格')


class CreateHasnTaskParam(HasnTaskSchemaBase):
    """创建任务定义参数"""


class UpdateHasnTaskParam(HasnTaskSchemaBase):
    """更新任务定义参数"""


class DeleteHasnTaskParam(SchemaBase):
    """删除任务定义参数"""

    pks: list[int] = Field(description='任务定义 ID 列表')


class GetHasnTaskDetail(HasnTaskSchemaBase):
    """任务定义详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
    builtin_update_available: bool = Field(
        False, description='官方目录有更新可手动拉取（读时 join catalog 派生，不落库）'
    )

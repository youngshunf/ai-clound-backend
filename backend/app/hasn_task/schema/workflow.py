"""工作流（任务图）Pydantic 模型（hasn_task 应用）。

create_workflow 一次声明整图：{name, goal, nodes:[...], edges:[...], schedule_*}。
节点复用 task（W3）；边用 parent/child node_key 引用（07 §9.1）。
"""

from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase

WORKFLOW_STATUS_DESCRIPTION = (
    '状态 (draft:草稿:gray/active:启用:green/paused:已暂停:orange/archived:已归档:gray/'
    'pending_approval:待审批:orange/rejected:已拒绝:gray)'
)


class WorkflowNodeSpec(SchemaBase):
    """工作流节点声明（= 一个 task 模板）"""

    node_key: str = Field(description='图内稳定节点标识，如 research-cost（同图唯一）')
    agent_id: str | None = Field(None, description='目标分身（可跨 Agent，W2；缺省=编排发起分身）')
    name: str | None = Field(None, description='节点名称（缺省取 node_key）')
    prompt: str = Field(description='节点任务指令')
    system_prompt: str | None = Field(None, description='节点系统提示词')
    description: str | None = Field(None, description='节点描述')
    skill_bundle_refs: list[dict] = Field(default_factory=list, description='节点技能包引用')
    skill_bundle_ids: list[str] = Field(default_factory=list, description='节点技能包名称列表')
    enabled_toolsets: list[str] | None = Field(None, description='节点限制工具集（NULL=全部；派发时取授权交集）')
    enable_subagents: bool = Field(False, description='允许节点会话内使用子分身 delegate_task')
    is_sink: bool = Field(False, description='标记为 sink 节点（整图 output_summary 取 sink 拼接）')
    # ↓ doc35 B1「修死列」：以下五个字段模板层早已声明、节点表也早有列，但建图入参一直没有它们，
    # 于是模板声明的产出闸/应用绑定在实例化时被整段丢弃，`workflow_node.output_spec` 永远是 NULL
    # ——闸「配了等于没配」。补进入参，让模板声明真正落到节点行上。
    output_spec: dict | None = Field(None, description='产出要求（doc35 §0.2 归一契约，见 OutputSpec）')
    review_policy: dict | None = Field(None, description='质量门策略')
    apps: list[str] = Field(default_factory=list, description='默认应用绑定 [app_id...]')
    skills: list[str] = Field(default_factory=list, description='默认技能绑定 [skill...]')
    is_origin: bool = Field(False, description='是否起点节点（主人输入锚点，预完成为 done、不过产出闸）')
    # display 呈现元数据 {order, step_label}：它决定端侧链路图的环号与阶段卡顺序，是展示数据不是
    # 附属品——doc35 B1 修死列时补了上面五个字段却漏了它，实例化后节点行 display 恒 {}，端侧只能
    # 按 node_key 字母序兜底编号（「市场调研」被排成第 8 环、落在「产品研发」之后）。
    display: dict | None = Field(None, description='呈现元数据 {order, step_label}（决定链路图环号顺序）')


class WorkflowEdgeSpec(SchemaBase):
    """工作流依赖边声明"""

    parent: str = Field(description='父节点 node_key')
    child: str = Field(description='子节点 node_key')


class CreateWorkflowParam(SchemaBase):
    """创建工作流参数（一次声明整图）"""

    owner_id: str = Field('', description='归属 owner（服务端以身份覆写）')
    workflow_uuid: str | None = Field(None, description='端云稳定工作流 UUID（缺省服务端生成）')
    name: str = Field(description='工作流名称')
    goal: str | None = Field(None, description='总目标')
    schedule_type: str = Field('once', description='整图定时 once/interval/cron')
    schedule_config: dict = Field(default_factory=dict, description='调度配置 JSON')
    schedule_display: str | None = Field(None, description='人类可读调度描述')
    timezone: str = Field('Asia/Shanghai', description='时区')
    misfire_policy: str = Field('run_once', description='错过补跑策略')
    catchup_limit: int | None = Field(None, description='补偿执行上限')
    continuation_enabled: bool = Field(False, description='跨 fire 接续（二期）')
    source: str = Field('owner', description='来源 owner/agent')
    created_by_kind: str = Field('owner', description='创建者类别 owner/agent/builtin')
    template_key: str | None = Field(None, description='场景实例化来源模板键；裸工程图为空')
    project_id: UUID | None = Field(None, description='所属平台项目；场景实例化必须为有效 UUID')
    instantiation_idempotency_key: str | None = Field(
        None, min_length=1, max_length=128, description='Owner 场景实例化幂等键；重放返回同一工作流'
    )
    status: str | None = Field(None, description='状态（缺省 active；agent 建定时图服务端置 pending_approval）')
    nodes: list[WorkflowNodeSpec] = Field(description='节点列表')
    edges: list[WorkflowEdgeSpec] = Field(default_factory=list, description='依赖边列表')


class WorkflowSchemaBase(SchemaBase):
    """工作流定义基础模型"""

    workflow_uuid: str = Field(description='端云稳定工作流 UUID')
    owner_id: str = Field(description='归属 owner')
    name: str = Field(description='工作流名称')
    goal: str | None = Field(None, description='总目标')
    schedule_type: str = Field('once', description='整图定时')
    schedule_config: dict = Field(default_factory=dict, description='调度配置')
    schedule_display: str | None = Field(None, description='调度描述')
    timezone: str = Field('Asia/Shanghai', description='时区')
    misfire_policy: str = Field('run_once', description='错过补跑策略')
    catchup_limit: int | None = Field(None, description='补偿执行上限')
    enabled: bool = Field(True, description='是否启用')
    status: str = Field('active', description=WORKFLOW_STATUS_DESCRIPTION)
    source: str = Field('owner', description='来源')
    created_by_kind: str = Field('owner', description='创建者类别')
    continuation_enabled: bool = Field(False, description='跨 fire 接续')
    next_run_at: datetime | None = Field(None, description='整图下次触发时间')
    last_run_at: datetime | None = Field(None, description='整图上次触发时间')
    workflow_revision: int = Field(0, description='工作流定义服务端修订号')


class GetWorkflowNode(SchemaBase):
    """工作流节点详情（图查询出参）"""

    model_config = ConfigDict(from_attributes=True)

    node_key: str
    agent_id: str
    name: str
    prompt: str | None = None
    system_prompt: str | None = None
    enable_subagents: bool = False
    task_uuid: str | None = None


class GetWorkflowDetail(WorkflowSchemaBase):
    """工作流详情（含节点 + 边）"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
    nodes: list[GetWorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdgeSpec] = Field(default_factory=list)


class GetWorkflowRunDetail(SchemaBase):
    """工作流执行实例详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    workflow_run_uuid: str
    workflow_uuid: str
    owner_id: str
    scheduled_fire_at: datetime | None = None
    dedupe_key: str
    status: str
    driver_node_id: str | None = None
    lease_expires_at: datetime | None = None
    output_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_time: datetime

"""工作流模板 Pydantic 模型（hasn_task 应用，P3 模板层 doc11 §4.2）。

- CreateWorkflowTemplateParam：建模板入参（分身 draft 工具 / hub 内置 seed / 测试共用；
  真实 draft/publish 工具在 P5，本切片仅供内置与测试构造）。
- WorkflowTemplatePublic：读 API 出参投影（列表/详情共用；详情才带 graph_spec）。
- WorkflowTemplateGraphSummary：从 graph_spec 派生的图摘要（节点数/应用数/阶段面包屑/人设列表），
  供首页模板条与画廊卡渲染，无须前端再解析 graph_spec。
"""

from uuid import UUID

from pydantic import Field

from backend.common.schema import SchemaBase


class WorkflowTemplateGraphSummary(SchemaBase):
    """图蓝图派生摘要（不含明细，供卡片渲染）"""

    node_count: int = Field(0, description='节点数')
    app_count: int = Field(0, description='去重后应用数（各节点 apps 并集）')
    apps: list[str] = Field(
        default_factory=list,
        description='去重后应用键（各节点 apps 并集·首见序）——供卡片渲染应用图标堆',
    )
    steps: list[dict] = Field(default_factory=list, description='阶段面包屑 [{label,order}]（按 order 排序）')
    agent_types: list[str] = Field(default_factory=list, description='涉及的默认人设类型（去重，非空）')


class WorkflowTemplatePublic(SchemaBase):
    """模板读 API 投影（列表/详情）"""

    template_key: str = Field(description='模板键（全局唯一）')
    template_uuid: str = Field(description='端云稳定模板 UUID（前缀 wft_）')
    domain: str | None = Field(None, description='领域分组 code（非空=场景模板）')
    name: str = Field(description='展示名')
    tagline: str | None = Field(None, description='一句话标签')
    description: str | None = Field(None, description='链路详述')
    sort_order: int = Field(0, description='展示排序')
    icon: str | None = Field(None, description='图标 key')
    accent: str | None = Field(None, description='主题强调色')
    status: str = Field(description='状态 draft/active/coming_soon/archived')
    source: str = Field(description='来源 builtin/owner/agent/marketplace')
    is_builtin: bool = Field(False, description='官方内置标记')
    builtin_key: str | None = Field(None, description='内置溯源键')
    owner_id: str | None = Field(None, description='归属主人（内置 NULL）')
    version: int = Field(1, description='模板版本')
    market_ref: str | None = Field(None, description='市场发布物溯源')
    sku_ref: str | None = Field(None, description='官方付费挂钩（NULL=免费；付费判权归 P7）')
    graph_summary: WorkflowTemplateGraphSummary = Field(
        default_factory=WorkflowTemplateGraphSummary, description='图蓝图派生摘要'
    )
    graph_spec: dict | None = Field(None, description='图蓝图全量（仅详情返回，列表为 None）')


class OwnerCreateTemplateParam(SchemaBase):
    """主人搭建器建模板入参（自定义场景全页搭建器 → POST /workflow-templates）。

    name + graph_spec 必填；status 二选一（draft 草稿 / active 上架），缺省 draft。
    其余展示字段可选。service 侧 create_owner_template 过 §6.3 校验后落 source=owner。
    """

    name: str = Field(description='展示名')
    graph_spec: dict = Field(description='图蓝图 {nodes:[...], edges:[...]}')
    domain: str | None = Field(None, description='领域分组 code（非空=场景模板）')
    tagline: str | None = Field(None, description='一句话标签')
    description: str | None = Field(None, description='链路详述')
    sort_order: int | None = Field(None, description='展示排序')
    icon: str | None = Field(None, description='图标 key')
    accent: str | None = Field(None, description='主题强调色')
    status: str | None = Field(None, description='状态 draft（草稿）/active（上架），缺省 draft')


class OwnerUpdateTemplateParam(SchemaBase):
    """主人搭建器改模板入参（PUT /workflow-templates/{key}）——全字段可选，只改显式传入的非 None 值。"""

    name: str | None = Field(None, description='展示名')
    graph_spec: dict | None = Field(None, description='图蓝图 {nodes:[...], edges:[...]}（传新版即过校验替换）')
    domain: str | None = Field(None, description='领域分组 code')
    tagline: str | None = Field(None, description='一句话标签')
    description: str | None = Field(None, description='链路详述')
    sort_order: int | None = Field(None, description='展示排序')
    icon: str | None = Field(None, description='图标 key')
    accent: str | None = Field(None, description='主题强调色')
    status: str | None = Field(None, description='状态 draft（草稿）/active（上架）')


class OwnerInstantiateTemplateParam(SchemaBase):
    """Owner 经 daemon 创建场景定义的权威入参（非幂等操作必须带幂等键）。"""

    project_id: UUID = Field(description='所属平台项目；服务端校验归属和未归档状态')
    idempotency_key: str = Field(min_length=1, max_length=128, description='同一主人重放返回同一工作流定义')
    goal: str = Field(min_length=1, description='本次场景目标')
    title: str | None = Field(None, description='实例标题；缺省取模板名')
    origin_input: str | None = Field(None, description='起点输入正文')
    default_agent_id: str | None = Field(None, description='未逐节点指定时使用的主人分身')
    node_overrides: dict[str, dict] = Field(
        default_factory=dict, description='逐节点覆盖 agent_id/prompt/system_prompt'
    )


class CreateWorkflowTemplateParam(SchemaBase):
    """建模板入参（内置 seed / 分身 draft / 测试共用）"""

    template_key: str = Field(description='模板键（全局唯一）')
    name: str = Field(description='展示名')
    domain: str | None = Field(None, description='领域分组 code（非空=场景模板）')
    tagline: str | None = Field(None, description='一句话标签')
    description: str | None = Field(None, description='链路详述')
    sort_order: int = Field(0, description='展示排序')
    icon: str | None = Field(None, description='图标 key')
    accent: str | None = Field(None, description='主题强调色')
    graph_spec: dict = Field(default_factory=dict, description='图蓝图 {nodes:[],edges:[]}')
    is_builtin: bool = Field(False, description='官方内置标记')
    builtin_key: str | None = Field(None, description='内置溯源键')
    status: str = Field('draft', description='状态（内置一般 active/coming_soon）')
    source: str = Field('owner', description='来源 builtin/owner/agent/marketplace')
    market_ref: str | None = Field(None, description='市场发布物溯源')
    sku_ref: str | None = Field(None, description='官方付费挂钩')
    version: int = Field(1, description='模板版本')

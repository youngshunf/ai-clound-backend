"""工作流节点定义模型（hasn_task.workflow_node）。

节点专属表（工作流应用产品化 P1 expand-only 迁移）：建图时物化，与借道的
hasn_task.task 节点行并存双写，读侧优先本表。字段承接 task 节点语义（agent/prompt/
技能/工具集）并新增产出闸（output_spec，P2）、质量门（review_policy，P4）、
默认人设（default_agent_type，P3）、起点、呈现元数据等声明位（本切片可空/默认）。

设计事实源：docs/hasn-node设计文档/12-任务系统实施方案/11-场景与工作流产品化.md。
"""

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_task.model._base import HasnTaskAppBase
from backend.common.model import UniversalText, id_key


class HasnWorkflowNode(HasnTaskAppBase):
    """工作流节点定义表"""

    __tablename__ = 'workflow_node'

    id: Mapped[id_key] = mapped_column(init=False)
    node_uuid: Mapped[str] = mapped_column(
        sa.String(64), default='', unique=True, comment='端云稳定节点 UUID（前缀 nd_，同步主键）'
    )
    workflow_uuid: Mapped[str] = mapped_column(sa.String(64), default='', comment='所属工作流稳定 UUID')
    owner_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='归属 owner')
    node_key: Mapped[str] = mapped_column(sa.String(64), default='', comment='图内稳定节点标识（同图唯一）')
    name: Mapped[str] = mapped_column(sa.String(200), default='', comment='节点名称（缺省取 node_key）')
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='节点描述')
    default_agent_type: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='默认人设类型（P3 用，可空）'
    )
    agent_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='解析后的目标分身 hasn_id')
    prompt: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='节点任务指令')
    system_prompt: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='节点系统提示词')
    apps: Mapped[list] = mapped_column(postgresql.JSONB(), default_factory=list, comment='默认应用绑定 [app_id...]')
    skills: Mapped[list] = mapped_column(postgresql.JSONB(), default_factory=list, comment='默认技能绑定 [skill...]')
    # 技能包与单技能是两个正交集合（运行时 RuntimeSkillRequirements.bundles / .skills 分别消费）。
    # 此前只有 skills 列：建图入参与 task 投影行都带着 skill_bundle_ids，唯独专属表没有落点，
    # 而读侧优先读本表 → 技能包在下发给 daemon 的 graph_snapshot 里被整段丢掉。
    skill_bundle_ids: Mapped[list] = mapped_column(
        postgresql.JSONB(), default_factory=list, comment='默认技能包绑定 [bundle_slug...]'
    )
    enabled_toolsets: Mapped[list | None] = mapped_column(
        postgresql.JSONB(), default=None, comment='限制工具集（NULL=全部；继承 task 语义，派发时取授权交集）'
    )
    output_spec: Mapped[dict | None] = mapped_column(
        postgresql.JSONB(), default=None, comment='产出闸声明 {kind,label}（P2，可空）'
    )
    review_policy: Mapped[dict | None] = mapped_column(
        postgresql.JSONB(),
        default=None,
        comment='质量门声明 {mode,criteria,reviewer_agent_type,max_rejects}（P4，可空）',
    )
    is_origin: Mapped[bool] = mapped_column(sa.Boolean(), default=False, comment='是否起点节点')
    display: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='呈现元数据 {order,step_label}'
    )
    max_retries: Mapped[int] = mapped_column(sa.INTEGER(), default=4, comment='最大重试次数')
    enable_subagents: Mapped[bool] = mapped_column(
        sa.Boolean(), default=False, comment='允许节点会话内使用子分身 delegate_task'
    )

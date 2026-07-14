"""工作流模板模型（hasn_task.workflow_template）。

模板层（工作流应用产品化 P3 · 场景即模板 doc11 §4.2）：模板声明领域链路蓝图
（graph_spec 节点+边）+ 领域皮肤元数据。domain 非空 = 场景模板（呈现走场景皮肤）；
NULL = 普通工作流模板。实例化时 graph_spec 物化为 workflow + workflow_node 行（真实物化
归 daemon 本地建再 sync 上云，云端只提供本表读 API 供其拉取蓝图）。

上架态不落本表——市场发布物是独立 listing 行（doc11 §8.2），本体只留 source/market_ref
溯源；官方付费模板挂 sku_ref → MK offering（对齐 hasn_app_catalog.sku_ref 应用付费先例）。

设计事实源：docs/hasn-node设计文档/12-任务系统实施方案/11-工作流应用产品化（场景即模板·直派工作会话·双闸·商业化）设计.md §4.2。
"""

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_task.model._base import HasnTaskAppBase
from backend.common.model import UniversalText, id_key

# 模板状态字典（value:label:color），与迁移 SQL COMMENT 保持一致
WORKFLOW_TEMPLATE_STATUS_COMMENT = (
    '状态 (draft:草稿:gray/active:启用:green/coming_soon:即将上线:orange/archived:已归档:gray)'
)
WORKFLOW_TEMPLATE_SOURCE_COMMENT = (
    '来源 (builtin:内置:gray/owner:主人自建:blue/agent:分身生成:violet/marketplace:市场物化:green)'
)


class HasnWorkflowTemplate(HasnTaskAppBase):
    """工作流模板表"""

    __tablename__ = 'workflow_template'

    id: Mapped[id_key] = mapped_column(init=False)
    template_uuid: Mapped[str] = mapped_column(
        sa.String(64), default='', unique=True, comment='端云稳定模板 UUID（前缀 wft_，同步主键）'
    )
    template_key: Mapped[str] = mapped_column(
        sa.String(64), default='', unique=True, comment='模板键（one_person_company/fin_research…），全局唯一'
    )
    domain: Mapped[str | None] = mapped_column(
        sa.String(32),
        default=None,
        comment='领域分组 code（startup/finance/office/professional…）；非空=场景模板走场景皮肤，NULL=普通工作流模板',
    )
    name: Mapped[str] = mapped_column(sa.String(64), default='', comment='展示名')
    tagline: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='一句话标签（画廊卡短语；与 description 并存）'
    )
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='链路详述')
    sort_order: Mapped[int] = mapped_column(
        sa.INTEGER(), default=0, comment='展示排序（首页模板条取前 N；排序第一的 active 场景模板即 hero 推荐位）'
    )
    icon: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='图标 key（lucide kebab 名）')
    accent: Mapped[str | None] = mapped_column(
        sa.String(16), default=None, comment='主题强调色（brand/teal/indigo/rose…）'
    )
    graph_spec: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='图蓝图 {nodes:[],edges:[]}（节点声明见 §4.3）'
    )
    is_builtin: Mapped[bool] = mapped_column(
        sa.Boolean(), default=False, comment='官方内置标记（对齐 hub 官方内置不变量）'
    )
    builtin_key: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='内置溯源键')
    status: Mapped[str] = mapped_column(
        sa.String(16), default='draft', comment=WORKFLOW_TEMPLATE_STATUS_COMMENT
    )
    owner_id: Mapped[str | None] = mapped_column(
        sa.String(40), default=None, comment='自定义模板归属主人（内置 NULL）'
    )
    source: Mapped[str] = mapped_column(
        sa.String(16), default='owner', comment=WORKFLOW_TEMPLATE_SOURCE_COMMENT
    )
    market_ref: Mapped[str | None] = mapped_column(
        sa.String(255), default=None, comment='市场发布物溯源 {market_template_id}@{version}（非市场来源 NULL）'
    )
    sku_ref: Mapped[str | None] = mapped_column(
        sa.String(64), default=None, comment='官方付费模板的 MK offering 挂钩；NULL=免费，仅 builtin 行用'
    )
    version: Mapped[int] = mapped_column(
        sa.INTEGER(), default=1, comment='模板版本（升级不影响在跑实例——实例化即物化节点行，天然快照）'
    )

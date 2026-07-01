from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnAppCatalog(Base):
    """AI-Native 应用目录（云端权威）"""

    __tablename__ = 'hasn_app_catalog'

    id: Mapped[id_key] = mapped_column(init=False)
    app_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='应用唯一标识（与 manifest.app_id / App.id 一致）')
    name: Mapped[str] = mapped_column(sa.String(64), default='', comment='显示名称')
    icon: Mapped[str] = mapped_column(sa.String(64), default='', comment='图标 token（前端 ICON_REGISTRY 映射）')
    icon_asset_uri: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='自定义图标资产 URI（优先于 token），hasn://asset/{id} 或 https')
    description: Mapped[str] = mapped_column(sa.String(255), default='', comment='应用描述')
    source: Mapped[str] = mapped_column(sa.String(16), default='', comment='来源 (builtin:内置:blue/first_party:官方:green/third_party:第三方:gray)')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='上架状态 (published:已上架:green/disabled:已下架:gray/draft:草稿:orange)')
    execution_mode: Mapped[str] = mapped_column(sa.String(24), default='', comment='执行形态 (cloud:云端:blue/embedded_desktop:桌面嵌入:green/local_tool:本地工具:gray)')
    scope: Mapped[list] = mapped_column(postgresql.JSONB(), default_factory=list, comment='可挂载空间类型 JSONB（personal/enterprise）')
    purchasable_by: Mapped[str] = mapped_column(
        sa.String(16),
        default='owner',
        comment='谁能买单 (owner:仅个人/enterprise:仅企业/both:双模)；仅 access_type=purchase 有约束意义（doc04 §1）',
    )
    collaboration_mode: Mapped[str] = mapped_column(sa.String(24), default='', comment='协作模式 (none:个人:gray/workspace_shared:空间共享:blue)')
    entry_route: Mapped[str] = mapped_column(sa.String(128), default='', comment='客户端原生路由')
    sort_order: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='工作台排序（小在前）')
    default_mount: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='新空间是否自动挂载（注册即用）')
    requires_role: Mapped[str | None] = mapped_column(sa.String(16), default=None, comment='企业空间所需角色（null=member 即可）')
    access_type: Mapped[str] = mapped_column(sa.String(16), default='', comment='准入类型 (free:免费:green/tier:订阅准入:blue/purchase:购买:orange)')
    min_tier: Mapped[str | None] = mapped_column(sa.String(16), default=None, comment='订阅准入所需最低档 (pro:专业版/advanced:进阶版/flagship:旗舰版)')
    price_amount: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, comment='购买价格（access_type=purchase 时）')
    price_unit: Mapped[str] = mapped_column(sa.String(8), default='', comment='计价单位 (cny:人民币:green/credits:积分:blue)')
    billing_cycle: Mapped[str] = mapped_column(sa.String(8), default='', comment='计费周期 (once:一次性:gray/month:包月:blue/year:包年:green)')
    trial_days: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='试用天数（0=无试用）')
    sku_ref: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='对接计费商品/订单 SKU（预留）')
    manifest_present: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=True, comment='是否有对应 code manifest（部署期回填）')
    default_agent_type: Mapped[str | None] = mapped_column(
        sa.String(64),
        default=None,
        comment='打开本应用默认承接的内置 agent 类型键(=marketplace_template.builtin_key)；NULL=回退主脑(AppCollab doc21 §4.3/§7)',
    )
    work_session_system_prompt: Mapped[str | None] = mapped_column(
        sa.Text(),
        default=None,
        comment='唤起分身时注入 work_session 的应用业务提示词(职责/产出形态/调用工具/零fake)，与本次指令组合(AppCollab doc21 §5.4)',
    )
    config_json: Mapped[dict] = mapped_column(
        postgresql.JSONB(),
        default_factory=dict,
        comment='应用专属平台级配置 JSON（每应用自治，如 film 5 类模型 failover + 引擎包 manifest 内联）；管理端直接编辑，platform-config 聚合下发',
    )
    # APPBETA-1：发布阶段（内测）+ 自定义角标。release_phase 与 status（上架/下架）正交：
    # 内测是「发布阶段」不是「上架状态」，灰度/全量内测应用仍需 status='published' 才进列表。
    release_phase: Mapped[str] = mapped_column(
        sa.String(16),
        default='ga',
        comment='发布阶段 (ga:正式:green/beta_full:全量内测:blue/beta_gray:灰度内测:orange)',
    )
    badge_text: Mapped[str | None] = mapped_column(
        sa.String(32), default=None, comment='自定义角标文字（如 热门/推荐/限免；空=无角标）'
    )
    badge_color: Mapped[str | None] = mapped_column(
        sa.String(16), default=None, comment='角标颜色 hex（如 #10B981；空=品牌默认紫）'
    )

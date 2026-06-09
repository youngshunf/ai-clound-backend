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
    app_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='应用唯一标识（与 manifest.app_id / WorkbenchApp.id 一致）')
    name: Mapped[str] = mapped_column(sa.String(64), default='', comment='显示名称')
    icon: Mapped[str] = mapped_column(sa.String(64), default='', comment='图标 token（前端 ICON_REGISTRY 映射）')
    icon_asset_uri: Mapped[str | None] = mapped_column(sa.String(255), default=None, comment='自定义图标资产 URI（优先于 token），hasn://asset/{id} 或 https')
    description: Mapped[str] = mapped_column(sa.String(255), default='', comment='应用描述')
    source: Mapped[str] = mapped_column(sa.String(16), default='', comment='来源 (builtin:内置:blue/first_party:官方:green/third_party:第三方:gray)')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='上架状态 (published:已上架:green/disabled:已下架:gray/draft:草稿:orange)')
    execution_mode: Mapped[str] = mapped_column(sa.String(24), default='', comment='执行形态 (cloud:云端:blue/embedded_desktop:桌面嵌入:green/local_tool:本地工具:gray)')
    scope: Mapped[list] = mapped_column(postgresql.JSONB(), default_factory=list, comment='可挂载空间类型 JSONB（personal/enterprise）')
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

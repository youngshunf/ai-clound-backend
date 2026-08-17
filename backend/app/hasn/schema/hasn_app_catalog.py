from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase

# 上架状态的封闭取值。**只用于写入面**（Create/Update），不下沉到基类——
# 基类同时是 GetHasnAppCatalogDetail 的父类，给它加枚举会让存量脏行在**读取**时 500。
CatalogStatus = Literal['published', 'disabled', 'draft']

# 发布阶段的封闭取值（与上架状态正交）。同样只用于写入面，理由同上。
# `demo` = 演示稿：应用中心可见可点开、页面是静态原型、分身工具全隐身（APPDEMO-1）。
CatalogReleasePhase = Literal['ga', 'beta_full', 'beta_gray', 'demo']


class HasnAppCatalogSchemaBase(SchemaBase):
    """AI-Native 应用目录（云端权威）基础模型"""

    app_id: str = Field(description='应用唯一标识（与 manifest.app_id / App.id 一致）')
    name: str = Field(description='显示名称')
    icon: str = Field(description='图标 token（前端 ICON_REGISTRY 映射）')
    icon_asset_uri: str | None = Field(
        None, description='自定义图标资产 URI（优先于 token），hasn://asset/{id} 或 https'
    )
    description: str = Field(description='应用描述')
    source: str = Field(description='来源 (builtin:内置:blue/first_party:官方:green/third_party:第三方:gray)')
    status: str = Field(description='上架状态 (published:已上架:green/disabled:已下架:gray/draft:草稿:orange)')
    execution_mode: str = Field(
        description='执行形态 (cloud:云端:blue/embedded_desktop:桌面嵌入:green/local_tool:本地工具:gray)'
    )
    scope: list = Field(description='可挂载空间类型 JSONB（personal/enterprise）')
    collaboration_mode: str = Field(description='协作模式 (none:个人:gray/workspace_shared:空间共享:blue)')
    entry_route: str = Field(description='客户端原生路由')
    sort_order: int = Field(description='工作台排序（小在前）')
    default_mount: bool = Field(description='新空间是否自动挂载（注册即用）')
    requires_role: str | None = Field(None, description='企业空间所需角色（null=member 即可）')
    access_type: str = Field(description='准入类型 (free:免费:green/tier:订阅准入:blue/purchase:购买:orange)')
    min_tier: str | None = Field(None, description='订阅准入所需最低档 (pro:专业版/advanced:进阶版/flagship:旗舰版)')
    price_amount: Decimal | None = Field(None, description='购买价格（access_type=purchase 时）')
    price_unit: str = Field(description='计价单位 (cny:人民币:green/credits:积分:blue)')
    billing_cycle: str = Field(description='计费周期 (once:一次性:gray/month:包月:blue/year:包年:green)')
    trial_days: int = Field(description='试用天数（0=无试用）')
    sku_ref: str | None = Field(None, description='对接计费商品/订单 SKU（预留）')
    manifest_present: bool = Field(description='是否有对应 code manifest（部署期回填）')
    config_json: dict = Field(
        default_factory=dict,
        description='应用专属平台级配置 JSON（每应用自治，如 film 5 类模型 failover + 引擎包 manifest 内联）；管理端直接编辑 JSON，platform-config 聚合下发',
    )
    # APPBETA-1：发布阶段（内测）+ 自定义角标。给默认值，旧管理端表单不传也合法。
    release_phase: str = Field(
        default='ga', description='发布阶段 (ga:正式:green/beta_full:全量内测:blue/beta_gray:灰度内测:orange)'
    )
    badge_text: str | None = Field(default=None, description='自定义角标文字（如 热门/推荐/限免；空=无角标）')
    badge_color: str | None = Field(default=None, description='角标颜色 hex（如 #10B981；空=品牌默认紫）')


class CreateHasnAppCatalogParam(HasnAppCatalogSchemaBase):
    """创建AI-Native 应用目录（云端权威）参数"""

    # 写入面收窄到三值，见 CatalogStatus。裸 str 时任何字符串都能落库，而
    # status != 'published' 会让应用从 list_published_catalog 消失——即从应用中心、
    # 工作台、分身工具面同时静默不见，且全程不报错。
    status: CatalogStatus = Field(description='上架状态 (published:已上架:green/disabled:已下架:gray/draft:草稿:orange)')
    release_phase: CatalogReleasePhase = Field(
        default='ga',
        description='发布阶段 (ga:正式:green/beta_full:全量内测:blue/beta_gray:灰度内测:orange/demo:演示:purple)',
    )


class UpdateHasnAppCatalogParam(HasnAppCatalogSchemaBase):
    """更新AI-Native 应用目录（云端权威）参数"""

    status: CatalogStatus = Field(description='上架状态 (published:已上架:green/disabled:已下架:gray/draft:草稿:orange)')
    release_phase: CatalogReleasePhase = Field(
        default='ga',
        description='发布阶段 (ga:正式:green/beta_full:全量内测:blue/beta_gray:灰度内测:orange/demo:演示:purple)',
    )


class UpdateHasnAppCatalogConfigParam(SchemaBase):
    """仅更新应用专属平台级配置 JSON（config_json）——管理端「编辑配置」专用。

    「编辑配置」只改 config_json 一项（如 reel/film 引擎的模型名），不应被迫回填整行（app_id/name/
    icon…），故独立 partial schema：只收 config_json，避免全字段 UpdateHasnAppCatalogParam 的必填校验。
    """

    config_json: dict = Field(
        description='应用专属平台级配置 JSON（每应用自治，如 reel/film 引擎模型）；platform-config 聚合下发'
    )


class DeleteHasnAppCatalogParam(SchemaBase):
    """删除AI-Native 应用目录（云端权威）参数"""

    pks: list[int] = Field(description='AI-Native 应用目录（云端权威） ID 列表')


class GetHasnAppCatalogDetail(HasnAppCatalogSchemaBase):
    """AI-Native 应用目录（云端权威）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None

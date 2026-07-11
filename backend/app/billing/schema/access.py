"""统一准入决策 AccessDecision（商业化内核唯一判定出参 · doc02 §3.1）。

resolve_access 的返回契约：一切付费墙判定（应用工具闸 / 订阅门 / 托管门 / 席位门）都返回
同一个 AccessDecision，前端/daemon/网关据此渲染付费墙、开工闸、试用入口，口径永不分叉。

设计事实源：docs/hasn-node设计文档/16-订阅与积分计费/02-统一商业化内核设计.md §3.1。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# AccessDecision.reason 的十个规范取值（doc02 §3.1）。reason 为开放字符串以兼容
# 席位/内测等结构性闸（need_seat_assignment/need_beta/need_enterprise_space 透传），
# 但商业化内核自身只产出以下十态。
CANONICAL_REASONS: frozenset[str] = frozenset(
    {
        'free',  # 免费/未门控 → 放行
        'tier_ok',  # 订阅档达标 → 放行
        'entitled',  # 持有效购买权益 → 放行
        'trialing',  # 试用期内 → 放行
        'need_upgrade',  # 订阅档不足 → 拦（引导升级）
        'need_purchase',  # 无权益 → 拦（引导购买）
        'trial_available',  # 无权益但可开试用 → 拦（引导试用）
        'quota_exceeded',  # 有权益但配额用尽 → 拦（引导扩容/升级）
        'disabled',  # 商品下架/停用 → 拦
        'expired_in_grace',  # 已过期但在宽限期内 → 暂放行（引导续费）
    }
)


class AccessOffer(BaseModel):
    """付费墙引导用的商品报价切片（指向 billing_offering + 默认 plan）。"""

    offering_key: str
    plan_key: str | None = None
    display_name: str = ''
    price: float | None = None
    price_unit: str = 'cny'
    cycle: str = 'once'
    trial: dict = Field(default_factory=dict)  # {enabled,days,times}
    purchase_uri: str = ''  # hasn://billing/offering/{key}（客户端无关深链）


class AccessQuota(BaseModel):
    """配额闸：snapshot=购买时固化的配额包；usage=当前用量（外部计量喂入）。"""

    snapshot: dict = Field(default_factory=dict)
    usage: dict = Field(default_factory=dict)


class AccessGrace(BaseModel):
    """宽限期：until=宽限截止（iso）；recoverable=订阅后是否可恢复原配额。"""

    until: str | None = None
    recoverable: bool = True


class AccessDecision(BaseModel):
    """统一准入决策（resolve_access 唯一出参）。"""

    model_config = ConfigDict(extra='forbid')

    allowed: bool
    reason: str
    feature_key: str
    requires: str | None = None  # purchase/upgrade/seat/beta/enterprise_space
    quota: AccessQuota | None = None
    grace: AccessGrace | None = None
    offer: AccessOffer | None = None
    # 兼容旧 AppAccess 消费面透传字段（MK-4 resolve_app_access 薄壳化用）
    min_tier: str | None = None
    trial_available: bool = False
    entitlement_expires_at: str | None = None

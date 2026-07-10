"""费用与账单中心聚合出参（doc03 · 实施/92 MK-7）。

`GET /billing/center` 一次性聚合「订阅快照 + 权益总账 + 提醒条」，让 daemon/webui 的
账单中心「概览」分区一发命中，不再多端点拼装。订单/积分流水沿用各自既有端点，不在此聚合。

设计事实源：docs/hasn-node设计文档/16-订阅与积分计费/03-费用与账单中心设计.md。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EntitlementLedgerItem(BaseModel):
    """权益总账单行（一条有效/历史权益的对外投影）。"""

    feature_key: str
    offering_key: str | None = None
    display_name: str = ''
    # 商品种类（llm_tier/credit_pack/app/seat/feature_plan）——取自 billing_offering.kind
    offering_kind: str | None = None
    subject_type: str = 'owner'  # owner / enterprise
    source: str = ''  # purchase / trial / admin_grant
    # 五态语义：active / trialing / in_grace / expired / revoked
    status: str = 'active'
    seats_total: int | None = None
    order_ref: str | None = None
    quota_snapshot: dict = Field(default_factory=dict)
    granted_at: str | None = None
    expires_at: str | None = None
    # 宽限期截止（仅 in_grace 态有值）
    grace_until: str | None = None


class BillingReminder(BaseModel):
    """账单提醒条数据（到期临近 / 宽限期中）。"""

    feature_key: str
    display_name: str = ''
    # expiring：即将到期；in_grace：已过期但在宽限期内（可续费恢复）
    kind: str
    at: str | None = None  # 到期时刻（expiring）或宽限截止（in_grace），iso
    days_left: int = 0


class BillingCenterResponse(BaseModel):
    """费用与账单中心聚合出参。"""

    model_config = ConfigDict(extra='forbid')

    # 订阅+积分快照（复用 credit_service.get_user_credits_info 的原语字典，全 JSON 安全）
    subscription: dict = Field(default_factory=dict)
    entitlements: list[EntitlementLedgerItem] = Field(default_factory=list)
    reminders: list[BillingReminder] = Field(default_factory=list)


class GrantTrialParam(BaseModel):
    """开通试用入参（对某 feature_key 发放一次试用）。"""

    feature_key: str

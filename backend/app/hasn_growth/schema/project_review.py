"""获客经营复盘、建议审阅与项目策略请求契约。"""

from typing import Literal

from pydantic import Field

from backend.common.schema import SchemaBase


class CreateGrowthReviewSuggestionBody(SchemaBase):
    """分身或系统提交下一周期建议。"""

    suggestion_kind: Literal['icp', 'channel', 'playbook']
    proposal: dict
    evidence: dict
    idempotency_key: str = Field(min_length=1, max_length=200)


class ReviewGrowthReviewSuggestionBody(SchemaBase):
    """Owner 接受或拒绝经营复盘建议。"""

    decision: Literal['accept', 'reject']


class UpdateGrowthReviewScheduleBody(SchemaBase):
    """Owner 显式启用或暂停周期经营复盘。"""

    enabled: bool


class UpdateGrowthProjectPolicyBody(SchemaBase):
    """Owner 修改静默时段、频控和预算策略。"""

    quiet_hours_start: int = Field(ge=0, le=23)
    quiet_hours_end: int = Field(ge=0, le=23)
    daily_outreach_limit: int = Field(ge=1, le=10000)
    monthly_budget: str | None = None
    budget_currency: str = Field(default='CNY', min_length=3, max_length=3)
    expected_policy_version: int = Field(ge=1)

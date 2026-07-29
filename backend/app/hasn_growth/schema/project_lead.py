"""S6 项目线索批次、筛选与状态变更契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from backend.common.schema import SchemaBase


class ProjectLeadScoreComponent(SchemaBase):
    """单个评分维度及其可审阅解释。"""

    score: float = Field(ge=0, le=100)
    explanation: str = Field(min_length=1, max_length=500)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)


class ProjectLeadPrivateChannel(SchemaBase):
    """仅在当前主体拥有合法来源时可写入的明文渠道。"""

    channel: str = Field(min_length=1, max_length=24)
    value: str = Field(min_length=1, max_length=500)
    lawful_basis: str = Field(min_length=1, max_length=48)
    source_ref: str = Field(min_length=1, max_length=255)
    consent_ref: str | None = Field(default=None, max_length=255)
    verified_at: datetime | None = None
    fresh_until: datetime | None = None


class ProjectLeadPrivateContact(SchemaBase):
    """当前 Owner 或企业自己的私有联系人资料。"""

    contact_name: str | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, max_length=100)
    lawful_basis: str = Field(min_length=1, max_length=48)
    source_ref: str = Field(min_length=1, max_length=255)
    consent_ref: str | None = Field(default=None, max_length=255)
    retention_until: datetime
    channels: list[ProjectLeadPrivateChannel] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def require_private_value(self) -> ProjectLeadPrivateContact:
        """私有资料至少包含姓名、职位或一个渠道，拒绝空壳授权记录。"""
        if not (
            (self.contact_name or '').strip()
            or (self.title or '').strip()
            or self.channels
        ):
            raise ValueError('私有联系人资料不能为空')
        return self


class ProjectLeadIngestItem(SchemaBase):
    """一条项目线索入池条目；公共事实与私有资料物理分流。"""

    client_ref: str = Field(min_length=1, max_length=64)
    lead_contact_id: int | None = Field(default=None, ge=1)
    company_name: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=500)
    domain: str | None = Field(default=None, max_length=255)
    country: str | None = Field(default=None, max_length=8)
    region: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    industry: str | None = Field(default=None, max_length=100)
    source_kind: str = Field(min_length=1, max_length=32)
    source_tool: str | None = Field(default=None, max_length=64)
    source_ref: str = Field(min_length=1, max_length=255)
    source_meta: dict[str, Any] = Field(default_factory=dict)
    match_score: float | None = Field(default=None, ge=0, le=100)
    score_breakdown: dict[str, ProjectLeadScoreComponent] = Field(default_factory=dict)
    scoring_version: str | None = Field(default=None, max_length=64)
    evidence_fresh_at: datetime | None = None
    private_contact: ProjectLeadPrivateContact | None = None

    @model_validator(mode='after')
    def require_contact_or_public_fact(self) -> ProjectLeadIngestItem:
        """既有联系人 ID 与可去重公共事实至少提供一种。"""
        if self.lead_contact_id is not None:
            return self
        if any(
            (value or '').strip()
            for value in (self.domain, self.website, self.company_name)
        ):
            return self
        raise ValueError('必须提供 lead_contact_id、企业域名、网站或企业名称')


class ProjectLeadBatchBody(SchemaBase):
    """Owner/Agent 共用的稳定批次输入。"""

    batch_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]*$')
    items: list[dict[str, Any]] = Field(min_length=1, max_length=100)


class ProjectLeadStatusBody(SchemaBase):
    """忽略或恢复项目线索。"""

    action: Literal['dismiss', 'restore']
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode='after')
    def require_dismiss_reason(self) -> ProjectLeadStatusBody:
        if self.action == 'dismiss' and not (self.reason or '').strip():
            raise ValueError('忽略线索必须填写原因')
        return self


class ProjectLeadAssignBody(SchemaBase):
    """企业经理分配或转移项目线索负责人。"""

    assignee: str = Field(min_length=1, max_length=40)

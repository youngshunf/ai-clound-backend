from __future__ import annotations

from typing import Any

from pydantic import Field

from backend.common.schema import SchemaBase


class CreateLeadJobParam(SchemaBase):
    keyword: str = Field(min_length=1, max_length=200, description='采集关键词或 URL')
    source_types: list[str] = Field(default_factory=lambda: ['public_web'], description='来源类型')
    user_id: int | None = Field(default=None, description='发起采集的用户 ID（run_job 跑完为其建 lead_ref）')
    max_pages: int = Field(default=5, ge=1, le=100)
    max_results: int = Field(default=100, ge=1, le=10000)
    request_config: dict[str, Any] = Field(default_factory=dict)


class ExportLeadParam(SchemaBase):
    filter_payload: dict[str, Any] = Field(default_factory=dict)
    user_id: int = Field(default=0)


class DsrEmailParam(SchemaBase):
    emails: list[str] = Field(min_length=1)
    request_id: str | None = None


class DsrPhoneParam(SchemaBase):
    phones: list[str] = Field(min_length=1)
    country_hint: str = 'CN'
    request_id: str | None = None

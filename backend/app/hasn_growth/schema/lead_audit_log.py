from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class LeadAuditLogSchemaBase(SchemaBase):
    """Lead automation PII and compliance audit log基础模型"""
    event_type: str = Field(description='None')
    actor_user_id: int | None = Field(None, description='None')
    actor_role: str | None = Field(None, description='None')
    actor_ip: str | None = Field(None, description='None')
    actor_ua: str | None = Field(None, description='None')
    target_table: str | None = Field(None, description='None')
    target_count: int = Field(description='None')
    target_ref: str | None = Field(None, description='None')
    payload: dict = Field(description='None')
    result: str = Field(description='None')
    error_message: str | None = Field(None, description='None')


class CreateLeadAuditLogParam(LeadAuditLogSchemaBase):
    """创建Lead automation PII and compliance audit log参数"""


class UpdateLeadAuditLogParam(LeadAuditLogSchemaBase):
    """更新Lead automation PII and compliance audit log参数"""


class DeleteLeadAuditLogParam(SchemaBase):
    """删除Lead automation PII and compliance audit log参数"""

    pks: list[int] = Field(description='Lead automation PII and compliance audit log ID 列表')


class GetLeadAuditLogDetail(LeadAuditLogSchemaBase):
    """Lead automation PII and compliance audit log详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None

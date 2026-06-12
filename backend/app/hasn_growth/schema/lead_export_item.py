from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class LeadExportItemSchemaBase(SchemaBase):
    """Lead CSV export item snapshot基础模型"""
    batch_id: int = Field(description='None')
    lead_contact_id: int = Field(description='None')
    lead_no: str = Field(description='None')
    snapshot: dict = Field(description='None')


class CreateLeadExportItemParam(LeadExportItemSchemaBase):
    """创建Lead CSV export item snapshot参数"""


class UpdateLeadExportItemParam(LeadExportItemSchemaBase):
    """更新Lead CSV export item snapshot参数"""


class DeleteLeadExportItemParam(SchemaBase):
    """删除Lead CSV export item snapshot参数"""

    pks: list[int] = Field(description='Lead CSV export item snapshot ID 列表')


class GetLeadExportItemDetail(LeadExportItemSchemaBase):
    """Lead CSV export item snapshot详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None

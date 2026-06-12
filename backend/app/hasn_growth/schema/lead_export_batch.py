from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class LeadExportBatchSchemaBase(SchemaBase):
    """Lead CSV export batch基础模型"""
    batch_no: str = Field(description='None')
    user_id: int = Field(description='None')
    lead_scope: str = Field(description='None')
    filter_payload: dict = Field(description='None')
    format: str = Field(description='None')
    total_count: int = Field(description='None')
    file_path: str | None = Field(None, description='None')
    file_sha256: str | None = Field(None, description='None')
    status: str = Field(description='None')
    error_message: str | None = Field(None, description='None')
    started_at: datetime | None = Field(None, description='None')
    finished_at: datetime | None = Field(None, description='None')


class CreateLeadExportBatchParam(LeadExportBatchSchemaBase):
    """创建Lead CSV export batch参数"""


class UpdateLeadExportBatchParam(LeadExportBatchSchemaBase):
    """更新Lead CSV export batch参数"""


class DeleteLeadExportBatchParam(SchemaBase):
    """删除Lead CSV export batch参数"""

    pks: list[int] = Field(description='Lead CSV export batch ID 列表')


class GetLeadExportBatchDetail(LeadExportBatchSchemaBase):
    """Lead CSV export batch详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None

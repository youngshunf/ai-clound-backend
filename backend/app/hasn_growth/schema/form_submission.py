from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class FormSubmissionSchemaBase(SchemaBase):
    """获客落地页表单回流（inbound 线索缓冲区）基础模型"""
    user_id: int = Field(description='None')
    publish_ref: str | None = Field(None, description='None')
    payload: dict = Field(description='None')
    email: str | None = Field(None, description='None')
    phone: str | None = Field(None, description='None')
    name: str | None = Field(None, description='None')
    company: str | None = Field(None, description='None')
    status: str = Field(description='状态 (pending:待处理:gray/converted:已转化:green/rejected:已拒绝:red/spam:垃圾:red)')
    customer_id: int | None = Field(None, description='None')
    source_meta: dict = Field(description='UTM/referrer/IP hash（反滥用 + 归因）')


class CreateFormSubmissionParam(FormSubmissionSchemaBase):
    """创建获客落地页表单回流（inbound 线索缓冲区）参数"""


class UpdateFormSubmissionParam(FormSubmissionSchemaBase):
    """更新获客落地页表单回流（inbound 线索缓冲区）参数"""


class DeleteFormSubmissionParam(SchemaBase):
    """删除获客落地页表单回流（inbound 线索缓冲区）参数"""

    pks: list[int] = Field(description='获客落地页表单回流（inbound 线索缓冲区） ID 列表')


class GetFormSubmissionDetail(FormSubmissionSchemaBase):
    """获客落地页表单回流（inbound 线索缓冲区）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None

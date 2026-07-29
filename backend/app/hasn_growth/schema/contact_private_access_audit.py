from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ContactPrivateAccessAuditSchemaBase(SchemaBase):
    """联系人私有资料访问的数据库追加式防篡改审计基础模型"""

    owner_scope: str = Field(description='None')
    user_id: int | None = Field(None, description='None')
    enterprise_id: int | None = Field(None, description='None')
    actor_type: str = Field(description='None')
    actor_id: str = Field(description='None')
    action: str = Field(description='None')
    resource_type: str = Field(description='None')
    resource_id: str = Field(description='None')
    purpose: str | None = Field(None, description='None')
    trace_id: str | None = Field(None, description='None')
    result: str = Field(description='None')
    denial_code: str | None = Field(None, description='None')
    request_metadata: dict = Field(description='只允许脱敏请求元数据，禁止联系方式、密文和令牌')


class CreateContactPrivateAccessAuditParam(ContactPrivateAccessAuditSchemaBase):
    """创建联系人私有资料访问的数据库追加式防篡改审计参数"""


class UpdateContactPrivateAccessAuditParam(ContactPrivateAccessAuditSchemaBase):
    """更新联系人私有资料访问的数据库追加式防篡改审计参数"""


class DeleteContactPrivateAccessAuditParam(SchemaBase):
    """删除联系人私有资料访问的数据库追加式防篡改审计参数"""

    pks: list[int] = Field(description='联系人私有资料访问的数据库追加式防篡改审计 ID 列表')


class GetContactPrivateAccessAuditDetail(ContactPrivateAccessAuditSchemaBase):
    """联系人私有资料访问的数据库追加式防篡改审计详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None

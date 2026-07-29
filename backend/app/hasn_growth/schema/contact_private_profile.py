from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ContactPrivateProfileSchemaBase(SchemaBase):
    """Owner 或企业对全局联系人的私有资料密文基础模型"""

    lead_contact_id: int = Field(description='None')
    owner_scope: str = Field(description='None')
    user_id: int | None = Field(None, description='None')
    enterprise_id: int | None = Field(None, description='None')
    contact_name_ciphertext: str | None = Field(None, description='联系人姓名应用层密文')
    title_ciphertext: str | None = Field(None, description='联系人职位应用层密文')
    encryption_key_version: int = Field(description='None')
    lawful_basis: str = Field(description='本主体取得和使用资料的合法依据')
    source_ref: str = Field(description='本主体取得资料的稳定来源引用')
    consent_ref: str | None = Field(None, description='None')
    retention_until: datetime = Field(description='资料允许保留到期时间')
    status: str = Field(description='None')


class CreateContactPrivateProfileParam(ContactPrivateProfileSchemaBase):
    """创建Owner 或企业对全局联系人的私有资料密文参数"""


class UpdateContactPrivateProfileParam(ContactPrivateProfileSchemaBase):
    """更新Owner 或企业对全局联系人的私有资料密文参数"""


class DeleteContactPrivateProfileParam(SchemaBase):
    """删除Owner 或企业对全局联系人的私有资料密文参数"""

    pks: list[int] = Field(description='Owner 或企业对全局联系人的私有资料密文 ID 列表')


class GetContactPrivateProfileDetail(ContactPrivateProfileSchemaBase):
    """Owner 或企业对全局联系人的私有资料密文详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None

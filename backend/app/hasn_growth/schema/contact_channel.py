from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ContactChannelSchemaBase(SchemaBase):
    """Owner 或企业授权持有的联系方式密文与版本化 HMAC基础模型"""

    private_profile_id: int = Field(description='None')
    lead_contact_id: int = Field(description='None')
    owner_scope: str = Field(description='None')
    user_id: int | None = Field(None, description='None')
    enterprise_id: int | None = Field(None, description='None')
    channel: str = Field(description='None')
    value_ciphertext: str = Field(description='联系方式应用层密文，禁止进入 Agent、日志和 daemon 缓存')
    encryption_key_version: int = Field(description='None')
    value_hmac: str = Field(description='使用独立服务端 HMAC 密钥计算的渠道匹配值')
    hash_key_version: int = Field(description='HMAC 密钥版本，轮换期支持多版本匹配')
    lawful_basis: str = Field(description='None')
    source_ref: str = Field(description='None')
    consent_ref: str | None = Field(None, description='None')
    verified_at: datetime | None = Field(None, description='None')
    fresh_until: datetime | None = Field(None, description='None')
    retention_until: datetime = Field(description='None')
    status: str = Field(description='None')


class CreateContactChannelParam(ContactChannelSchemaBase):
    """创建Owner 或企业授权持有的联系方式密文与版本化 HMAC参数"""


class UpdateContactChannelParam(ContactChannelSchemaBase):
    """更新Owner 或企业授权持有的联系方式密文与版本化 HMAC参数"""


class DeleteContactChannelParam(SchemaBase):
    """删除Owner 或企业授权持有的联系方式密文与版本化 HMAC参数"""

    pks: list[int] = Field(description='Owner 或企业授权持有的联系方式密文与版本化 HMAC ID 列表')


class GetContactChannelDetail(ContactChannelSchemaBase):
    """Owner 或企业授权持有的联系方式密文与版本化 HMAC详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
